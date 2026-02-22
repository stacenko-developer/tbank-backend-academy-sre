#!/usr/bin/env python3

import logging
import signal
import sys
import time
import requests
import mysql.connector

from datetime import datetime
from environs import Env
from typing import Dict, Final, Optional

from prometheus_client import start_http_server, Gauge

env = Env()
env.read_env()

DELETE_TEAM_SCENARIO_SUCCESS_PERCENT_METRIC_NAME = "sla_calculator_delete_team_scenario_success_percent"
P98_CREATE_TEAM_DURATION_MILLIS_METRIC_NAME = "sla_calculator_p98_create_team_duration_millis"

SUCCESS_SLO_STATUS = "SUCCESS"
FAIL_SLO_STATUS = "FAIL"

SLA_CALCULATOR_DELETE_TEAM_SCENARIO_SUCCESS_PERCENT = Gauge(DELETE_TEAM_SCENARIO_SUCCESS_PERCENT_METRIC_NAME, 'SLA delete team success percent %')
SLA_CALCULATOR_P98_CREATE_TEAM_DURATION_MILLIS = Gauge(P98_CREATE_TEAM_DURATION_MILLIS_METRIC_NAME, 'SLA P98 duration millis')


class Config(object):
    sage_url = env("SAGE_API_URL", "https://sage.sre-ab.ru/mage/api")
    scrape_interval = env.int("SCRAPE_INTERVAL", 60)
    log_level = env.log_level("LOG_LEVEL", logging.DEBUG)
    metrics_port = env.int("METRICS_PORT", 9082)

    mysql_host = env("MYSQL_HOST", 'localhost')
    mysql_port = env.int("MYSQL_PORT", '3306')
    mysql_user = env("MYSQL_USER", 'root')
    mysql_password = env("MYSQL_PASSWORD", '1234')
    mysql_db_name = env("MYSQL_DB_NAME", 'sla')

    success_deletion_team_percent_sla = env.int("SUCCESS_REMOVE_TEAM_PERCENT_SLA", 99)
    p98_creation_team_duration_millis_sla = env.int("P98_CREATION_TEAM_DURATION_MILLIS_SLA", 40)

    sage_auth_token = env("SAGE_AUTH_TOKEN", "Bearer eyJraWQiOiI2REFFREQ5Q0M5RUIxMDcyQUVDQTE4Qzg1RjMwNERFRDdGMEEyNDkxRERDRTYyNDk5RjlDRTkzRjlEOEJEODI1IiwidHlwIjoiSldUIiwiYWxnIjoiUlMyNTYifQ.eyJpc3MiOiJtYW51bCIsImV4cCI6NDEwMjQ0NDgwMCwiaWF0IjoxNzYyMjgyNzM5LCJncm91cCI6WyJzYWdlX2FiNV9zdGF0c2Vua28iXSwidXBuIjoiYWI1X3N0YXRzZW5rb0BrZXljbG9hay5sb2NhbGhvc3QiLCJwZXJtaXNzaW9ucyI6W10sImNsaWVudElkIjoiYWI1X3N0YXRzZW5rbyIsImp0aSI6IjVkMmI5MDIxLTViZGEtNDczMS05MTdmLTFkNzQwY2U0YjgzNCJ9.NNpYQsRg6HI-43Pfz0jMdOWccASDKydFQDfRXxepLRc9WeDclFYkCyIBXIGN-czaz7_Gz_2VKzKUOPGrjgz6ET_eCv_BdqBrrm8omqlVTK4zD8ebO6cOGkXTqegbVErNb5q8_NmbOSRncTCX_mA5tIFLxnEN_Aokn9lkPzESy5qzR6e5GnNVHdZFnJNOGZZi4d00qb92JSWbDOssa_iiX4f9-XxqqG6PTjF-z5rF9xENo_7yCQQoOXuTopX5AiMKwhmI_wT6bkmasmUPbZzknmM5i71SElqvSCQ_24aoSjPK1ABPgvPMm5WN0bLdL6ot5PnR_EHDD6VGfeDC6MNH9Q")


class SlaRepository:

    __INSERT_INDICATOR_QUERY = """
        INSERT INTO {table_name} (name, slo, value, is_bad, datetime)
        VALUES (%s, %s, %s, %s, %s)
    """

    def __init__(self, config: Config) -> None:
        logging.info('Connecting db')
        self.__connection = mysql.connector.connect(host=config.mysql_host, user=config.mysql_user,
                                                    passwd=config.mysql_password, auth_plugin='mysql_native_')
        self.__table_name = 'indicators'
        logging.info('Starting migration')
        cursor = self.__connection.cursor()
        cursor.execute('CREATE DATABASE IF NOT EXISTS %s' % config.mysql_db_name)
        cursor.execute('USE %s' % config.mysql_db_name)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS %s(
                datetime datetime not null default NOW(),
                name varchar(255) not null,
                slo float(4) not null,
                value float(4) not null,
                is_bad bool not null default false
            )
        """ % self.__table_name)

    def save_indicator(self, name, slo, value, is_bad=False, current_time_with_iso_format=None) -> None:
        cursor = self.__connection.cursor()
        sql = SlaRepository.__INSERT_INDICATOR_QUERY.format(table_name=self.__table_name)
        val = (name, slo, value, is_bad, current_time_with_iso_format)

        cursor.execute(sql, val)
        self.__connection.commit()
        cursor.close()

class SageClient:

    __SEARCH_URL_FORMAT: Final[str] = "%s/search"

    __AUTHORIZATION_HEADER: Final[str] = "Authorization"
    __SOURCE_HEADER: Final[str] = "Source"

    __SERVICE_NAME: Final[str] = "oncall-sla-calculator-service"

    __QUERY_JSON_FIELD: Final[str] = "query"
    __SIZE_JSON_FIELD: Final[str] = "size"
    __START_TIME_JSON_FIELD: Final[str] = "startTime"
    __END_TIME_JSON_FIELD: Final[str] = "endTime"

    __HITS_JSON_FIELD: Final[str] = "hits"
    __VALUE_JSON_FIELD: Final[str] = "value"

    __URL_NOT_BE_EMPTY_MESSAGE: Final[str] = "URL cannot be empty"
    __TOKEN_NOT_BE_EMPTY_MESSAGE: Final[str] = "Token cannot be empty"

    __DEFAULT_RESULT_SIZE: Final[int] = 100
    __URL_PATH_SEPARATOR = "/"

    def __init__(self, url, token) -> None:
        self.__url = url.rstrip(SageClient.__URL_PATH_SEPARATOR)
        self.__token = token

    def last_value(self, query, current_time_with_iso_format) -> Optional[float]:
        try:
            response = requests.post(
                SageClient.__SEARCH_URL_FORMAT % self.__url, headers=self.__get_headers(), json={
                    SageClient.__QUERY_JSON_FIELD: query,
                    SageClient.__SIZE_JSON_FIELD: SageClient.__DEFAULT_RESULT_SIZE,
                    SageClient.__START_TIME_JSON_FIELD: current_time_with_iso_format,
                    SageClient.__END_TIME_JSON_FIELD: current_time_with_iso_format})
            content = response.json()
            if not content:
                return None
            if len(content[SageClient.__HITS_JSON_FIELD]) == 0:
                return None
            return content[SageClient.__HITS_JSON_FIELD][0][SageClient.__VALUE_JSON_FIELD]
        except Exception as error:
            logging.error(error)
            return None

    def __get_headers(self) -> Dict[str, str]:
        return {
            SageClient.__AUTHORIZATION_HEADER: self.__token,
            SageClient.__SOURCE_HEADER: SageClient.__SERVICE_NAME
        }

    @staticmethod
    def __validate_init_arguments(url: str, token: str) -> None:
        if not url or not url.strip():
            raise ValueError(SageClient.__URL_NOT_BE_EMPTY_MESSAGE)
        if not token or not token.strip():
            raise ValueError(SageClient.__TOKEN_NOT_BE_EMPTY_MESSAGE)

class SlaCalculator:

    __DELETE_TEAM_SCENARIO_TOTAL_QUERY = "pql increase(prober_delete_team_scenario_total{system=\"oncall-prober-service\", group=\"ab5_statsenko\"}[1m])"
    __DELETE_TEAM_SCENARIO_SUCCESS_TOTAL_QUERY = "pql increase(prober_delete_team_scenario_success_total{system=\"oncall-prober-service\", group=\"ab5_statsenko\"}[1m])"

    __CREATE_TEAM_DURATION_P98_QUERY = "pql quantile_over_time(0.98, prober_create_team_scenario_duration_milliseconds{system=\"oncall-prober-service\", group=\"ab5_statsenko\"}[1m])"

    def __init__(self, sage_client: SageClient) -> None:
        self.__sage_client = sage_client

    def calculate_success_team_deletion_percent(self, iso_format_with_tz: str) -> int:
        delete_team_success_total = self.__sage_client.last_value(
            SlaCalculator.__DELETE_TEAM_SCENARIO_SUCCESS_TOTAL_QUERY,
            iso_format_with_tz)

        if delete_team_success_total is None:
            logging.error("No successful deletion count data available")
            return 0

        logging.debug(f"Count of success deletion team is {delete_team_success_total}")

        delete_team_total = self.__sage_client.last_value(
            SlaCalculator.__DELETE_TEAM_SCENARIO_TOTAL_QUERY,
            iso_format_with_tz)

        if delete_team_total is None:
            logging.error("No total deletion count data available")
            return 0

        logging.debug(f"Count of deletion team is {delete_team_total}")

        if delete_team_total == 0:
            return 0

        return int(delete_team_success_total / delete_team_total * 100)

    def calculate_team_creation_duration_p98(self, iso_format_with_tz: str) -> float:
        team_creation_duration_p98 = self.__sage_client.last_value(
            SlaCalculator.__CREATE_TEAM_DURATION_P98_QUERY,
            iso_format_with_tz)

        if team_creation_duration_p98 is None:
            logging.error("No p98 team creation duration data available")
            return 0

        return team_creation_duration_p98


def setup_logging(config: Config) -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=config.log_level,
        format="%(asctime)s %(levelname)s:%(message)s")

def main() -> None:
    config = Config()
    setup_logging(config)
    sla_repository = SlaRepository(config)
    sage_client = SageClient(config.sage_url, config.sage_auth_token)
    sla_calculator = SlaCalculator(sage_client)
    logging.info(f"Starting sla checker")
    start_http_server(config.metrics_port)
    logging.info(f"Starting sla calculator exporter on port: {config.metrics_port}")

    while True:
        logging.debug(f"Run oncall sla calculator")

        unix_timestamp = time.time()
        iso_format_with_tz = datetime.fromtimestamp(unix_timestamp).astimezone().isoformat()

        success_team_deletion_percent = sla_calculator.calculate_success_team_deletion_percent(iso_format_with_tz)
        slo_status = FAIL_SLO_STATUS if success_team_deletion_percent < config.success_deletion_team_percent_sla else SUCCESS_SLO_STATUS
        logging.debug(f"Percent of success team deletion is {success_team_deletion_percent}%. SLO is {config.success_deletion_team_percent_sla}% {slo_status}")
        sla_repository.save_indicator(name=DELETE_TEAM_SCENARIO_SUCCESS_PERCENT_METRIC_NAME,
                                          slo=config.success_deletion_team_percent_sla,
                                          value=success_team_deletion_percent,
                                          is_bad=slo_status == FAIL_SLO_STATUS,
                                          current_time_with_iso_format=iso_format_with_tz)
        SLA_CALCULATOR_DELETE_TEAM_SCENARIO_SUCCESS_PERCENT.set(success_team_deletion_percent)

        team_creation_duration_p98 = sla_calculator.calculate_team_creation_duration_p98(iso_format_with_tz)
        slo_status = FAIL_SLO_STATUS if team_creation_duration_p98 == 0 or team_creation_duration_p98 > config.p98_creation_team_duration_millis_sla else SUCCESS_SLO_STATUS
        logging.debug(f"p98 of team creation duration is {team_creation_duration_p98}. SLO is {config.p98_creation_team_duration_millis_sla} {slo_status}")
        sla_repository.save_indicator(name=P98_CREATE_TEAM_DURATION_MILLIS_METRIC_NAME,
                                              slo=config.p98_creation_team_duration_millis_sla,
                                              value=team_creation_duration_p98,
                                              is_bad=slo_status == FAIL_SLO_STATUS,
                                              current_time_with_iso_format=iso_format_with_tz)
        SLA_CALCULATOR_P98_CREATE_TEAM_DURATION_MILLIS.set(team_creation_duration_p98)

        logging.debug(f"Waiting {config.scrape_interval} seconds for next loop")
        time.sleep(config.scrape_interval)

def terminate(signal, frame) -> None:
    print("Terminating")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    main()