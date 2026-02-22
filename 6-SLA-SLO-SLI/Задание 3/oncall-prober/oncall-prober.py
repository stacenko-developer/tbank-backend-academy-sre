#!/usr/bin/env python3

import logging
import signal
import sys
import time
import requests

from multiprocessing import AuthenticationError
from environs import Env
from typing import Dict, Final
from prometheus_client import Counter, Gauge, start_http_server
from requests import Response

PROBER_DELETE_TEAM_SCENARIO_TOTAL = Counter(
    "prober_delete_team_scenario_total", "Total count of runs the delete team scenario to oncall API"
)
PROBER_DELETE_TEAM_SCENARIO_SUCCESS_TOTAL = Counter(
    "prober_delete_team_scenario_success_total", "Total count of success runs the delete team scenario to oncall API"
)
PROBER_DELETE_TEAM_SCENARIO_FAIL_TOTAL = Counter(
    "prober_delete_team_scenario_fail_total", "Total count of failed runs the delete team scenario to oncall API"
)
PROBER_CREATE_TEAM_SCENARIO_DURATION_MILLISECONDS = Gauge(
    "prober_create_team_scenario_duration_milliseconds", "Duration in milliseconds of runs the create team scenario to oncall API"
)

class OncallProberClient:

    __DEFAULT_TEAM_NAME: Final[str] = "team-foo"

    __LOGIN_URL_FORMAT: Final[str] = "%s/login"
    __TEAM_URL_FORMAT: Final[str] = "%s/api/v0/teams"
    __DELETE_TEAM_URL_FORMAT: Final[str] = f"{__TEAM_URL_FORMAT}/{__DEFAULT_TEAM_NAME}"

    __SET_COOKIE_HEADER: Final[str] = "set-cookie"
    __X_CSRF_TOKEN_HEADER: Final[str] = "X-CSRF-TOKEN"
    __COOKIE_HEADER: Final[str] = "Cookie"

    __CSRF_TOKEN: Final[str] = "csrf_token"

    __DEFAULT_TEAM_TEMPLATE: Final[dict[str, str]] = {
        "name": __DEFAULT_TEAM_NAME,
        "scheduling_timezone": "US/Pacific",
        "email": f"{__DEFAULT_TEAM_NAME}@example.com",
        "slack_channel": f"#{__DEFAULT_TEAM_NAME}"
    }

    __MAX_LOGIN_ATTEMPTS: Final[int] = 3
    __LOGIN_RETRY_DELAY_SECONDS: Final[int] = 10

    __URL_NOT_BE_EMPTY_MESSAGE: Final[str] = "URL cannot be empty"
    __USERNAME_NOT_BE_EMPTY_MESSAGE: Final[str] = "Username cannot be empty"
    __PASSWORD_NOT_BE_EMPTY_MESSAGE: Final[str] = "Password cannot be empty"

    __URL_PATH_SEPARATOR = "/"

    def __init__(self, url: str, username: str, password: str) -> None:
        OncallProberClient.__validate_init_arguments(url, username, password)

        self.__url = url.rstrip(OncallProberClient.__URL_PATH_SEPARATOR)
        self.__cookies = None
        self.__csrf_token = None
        self.__username = username
        self.__password = password
        self.__login_attempts = 0

        self.__login(self.__username, self.__password)

    @staticmethod
    def __validate_init_arguments(url: str, username: str, password: str) -> None:
        if not url or not url.strip():
            raise ValueError(OncallProberClient.__URL_NOT_BE_EMPTY_MESSAGE)
        if not username or not username.strip():
            raise ValueError(OncallProberClient.__USERNAME_NOT_BE_EMPTY_MESSAGE)
        if not password or not password.strip():
            raise ValueError(OncallProberClient.__PASSWORD_NOT_BE_EMPTY_MESSAGE)

    def __login(self, username: str, password: str) -> None:
        login_response = requests.post(OncallProberClient.__LOGIN_URL_FORMAT % self.__url, data=f"username={username}&password={password}")

        if login_response.status_code == requests.codes.ok:
            self.__cookies = login_response.headers.get(OncallProberClient.__SET_COOKIE_HEADER)
            self.__csrf_token = login_response.json()[OncallProberClient.__CSRF_TOKEN]
            self.__login_attempts = 0
            return

        self.__login_attempts += 1

        if self.__login_attempts >= OncallProberClient.__MAX_LOGIN_ATTEMPTS:
            raise AuthenticationError(f"All login attempts failed. Last status: {login_response.status_code}")

        logging.error(f"Failed to authenticate with status {login_response.status_code} and response {login_response.text}. Retry in {OncallProberClient.__LOGIN_RETRY_DELAY_SECONDS} seconds")
        time.sleep(OncallProberClient.__LOGIN_RETRY_DELAY_SECONDS)
        self.__login(username, password)

    def __get_headers(self) -> Dict[str, str]:
        return {
            OncallProberClient.__X_CSRF_TOKEN_HEADER: self.__csrf_token,
            OncallProberClient.__COOKIE_HEADER: self.__cookies
        }

    def __create_default_team(self) -> Response:
        return requests.post(OncallProberClient.__TEAM_URL_FORMAT % self.__url,
                      headers=self.__get_headers(),
                      json=OncallProberClient.__DEFAULT_TEAM_TEMPLATE)

    def __delete_default_team(self) -> Response:
        return requests.delete(OncallProberClient.__DELETE_TEAM_URL_FORMAT % self.__url,
                               headers=self.__get_headers())

    def __execute_team_creation(self) -> None:
        logging.debug("try create team")
        start = time.perf_counter()

        try:
            create_team_response = self.__create_default_team()
            duration_milliseconds = (time.perf_counter() - start) * 1000
            logging.debug(f"create team in {duration_milliseconds} milliseconds")

            PROBER_CREATE_TEAM_SCENARIO_DURATION_MILLISECONDS.set(duration_milliseconds)

            if create_team_response:
                logging.debug(f"create team with status {create_team_response.status_code}, response: {create_team_response.text}")
        except Exception as err:
            logging.error(err)
            duration_milliseconds = (time.perf_counter() - start) * 1000
            logging.error(f"time spent trying to create team is {duration_milliseconds} milliseconds")
            PROBER_CREATE_TEAM_SCENARIO_DURATION_MILLISECONDS.set(duration_milliseconds)

    def __execute_team_deletion(self) -> None:
        delete_team_response = None

        try:
            PROBER_DELETE_TEAM_SCENARIO_TOTAL.inc()
            logging.debug("try delete team")
            delete_team_response = self.__delete_default_team()

            if delete_team_response:
                logging.debug(f"delete team with status {delete_team_response.status_code}, response: {delete_team_response.text}")
        except Exception as err:
            logging.error(err)

        if delete_team_response and delete_team_response.status_code == requests.codes.ok:
            PROBER_DELETE_TEAM_SCENARIO_SUCCESS_TOTAL.inc()
        else:
            PROBER_DELETE_TEAM_SCENARIO_FAIL_TOTAL.inc()

    def probe(self) -> None:
        self.__execute_team_creation()
        self.__execute_team_deletion()

env = Env()
env.read_env()

class Config(object):
    oncall_url = env("ONCALL_URL", "http://oncall.st-ab5-statsenko.ingress.sre-ab.ru/")
    scrape_interval = env.int("SCRAPE_INTERVAL", 30)
    log_level = env.log_level("LOG_LEVEL", logging.DEBUG)
    metrics_port = env.int("METRICS_PORT", 9081)
    oncall_username = env("ONCALL_USERNAME", "root")
    oncall_password = env("ONCALL_PASSWORD", "1234")

def setup_logging(config: Config):
    logging.basicConfig(
        stream=sys.stdout,
        level=config.log_level,
        format="%(asctime)s %(levelname)s:%(message)s"
    )

def main():
    config = Config()
    setup_logging(config)
    start_http_server(config.metrics_port)

    logging.info(f"Starting prober exporter on port: {config.metrics_port}")
    client = OncallProberClient(config.oncall_url, config.oncall_username, config.oncall_password)

    while True:
        logging.debug(f"Run prober")
        client.probe()

        logging.debug(f"Waiting {config.scrape_interval} seconds for next loop")
        time.sleep(config.scrape_interval)

def terminate(signal, frame):
    print("Terminating")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    main()