# Проблемы, обнаруженные в манифестах

## Манифесты mysql

### secret.yaml
```
apiVersion: v1
kind: Secret
metadata:
  name: mysql
  namespace: st-ab5-statsenko
type: Opaque
data:
  MYSQL_ROOT_PASSWORD: MTIzNA==
```
1. Содержимое секретов хранится в base64 в etcd. Хранение секретов в таком виде небезопасно, содержимое легко читается из etcd при компрометации. Следует включить шифрование.
2. Отсутствие RBAC для разграничения доступа к сущностям в Kubernetes - содержимое секретов легко читается - необходимо настроить RBAC правила для доступа к секретам. 

Также можно рассмотреть использование внешних хранилищ для хранения секретов. Например, HashiCorp Vault. 

### statefulset.yaml
```
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: mysql
  namespace: st-ab5-statsenko
spec:
  selector:
    matchLabels:
      app: mysql
      app.kubernetes.io/name: mysql
  serviceName: mysql
  replicas: 1
  template:
    metadata:
      labels:
        app: mysql
        app.kubernetes.io/name: mysql
    spec:
      containers:
        - name: mysql
          image: mysql:8.3.0
          envFrom:
            - secretRef:
                name: mysql
          ports:
            - name: mysql
              containerPort: 3306
          volumeMounts:
            - name: data
              mountPath: /var/lib/mysql
              subPath: mysql
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
          livenessProbe:
            tcpSocket:
              port: 3306
            initialDelaySeconds: 30
            periodSeconds: 10
            timeoutSeconds: 5
          readinessProbe:
            tcpSocket:
              port: 3306
            initialDelaySeconds: 5
            periodSeconds: 2
            timeoutSeconds: 1
      volumes:
        - name: data
          emptyDir: {}
```
1. EmptyDir - временное хранилище, которое удаляется при перезапуске Pod. Это грозит полной потерей данных, поэтому EmptyDir не подходит для персистентного хранилища. В качестве персистентного хранилища следует использовать PersistentVolumeClaim (PVC).
2. Все ключи из секрета mysql становятся переменными окружения в контейнере. Необходимо явно указывать только необходимые переменные.
3. TCP-проверка в livenessProbe и readinessProbe только подтверждает, что порт открыт, но не проверяет, что MySQL может принимать подключения, отвечать на запросы. Следует проверять именно это.

## Манифесты oncall

### config.yaml
```
apiVersion: v1
kind: ConfigMap
metadata:
  name: oncall
  namespace: st-ab5-statsenko
data:
  oncall.conf: |
    ---
    server:
      host: 0.0.0.0
      port: 8080
    debug: True
    oncall_host: oncall.st-ab5-statsenko.ingress.sre-ab.ru
    metrics: dummy
    db:
      conn:
        kwargs:
          scheme: mysql+pymysql
          user: root
          password: '1234'
          host: oncall-mysql
          port: 3306
          database: oncall
          charset: utf8
          echo: True
        str: "%(scheme)s://%(user)s:%(password)s@%(host)s/%(database)s?charset=%(charset)s"
      kwargs:
        pool_recycle: 3600
    session:
      encrypt_key: 'abc'
      sign_key: '123'
    auth:
      debug: False
      module: 'oncall.auth.modules.debug'
    notifier:
      skipsend: True
    healthcheck_path: /tmp/status
    messengers:
      - type: dummy
        application: oncall
        iris_api_key: magic

    allow_origins_list:
     - http://oncall.st-ab5-statsenko.ingress.sre-ab.ru

    supported_timezones:
      - 'US/Pacific'
      - 'US/Eastern'
      - 'US/Central'
      - 'US/Mountain'
      - 'US/Alaska'
      - 'US/Hawaii'
      - 'Asia/Kolkata'
      - 'Asia/Shanghai'
      - 'UTC'

    index_content_setting:
    #footer: |
    #  <ul>
    #    <li>Oncall © LinkedIn 2020</li>
    #    <li>Feedback</li>
    #    <li><a href="http://oncall.tools" target="_blank">About</a></li>
    #  </ul>
      missing_number_note: 'No number'

    notifications:
      default_roles:
        - "primary"
        - "secondary"
        - "shadow"
        - "manager"
      default_times:
        - 86400
        - 604800
      default_modes:
        - "email"

    reminder:
      activated: True
      polling_interval: 360
      default_timezone: 'US/Pacific'

    user_validator:
      activated: True
      subject: 'Warning: Missing phone number in Oncall'
      body: 'You are scheduled for an on-call shift in the future, but have no phone number recorded. Please update your information in Oncall.'

    slack_instance: foobar
    header_color: '#3a3a3a'
    team_managed_message: 'Managed team - this team is managed via API'
```
1. Пароли передаются в открытом виде. Необходимо хранить их в Secret с использованием шифрования или использовать внешние хранилища для хранения секретов.
2. debug: True означает, что приложение может логировать конфиденциальные данные, выдавать подробные ошибки, следует сделать debug: False.

### deployment.yaml
```
apiVersion: apps/v1
kind: Deployment
metadata:
  name: oncall
  namespace: st-ab5-statsenko
  labels:
    app.kubernetes.io/name: oncall
    app.kubernetes.io/component: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: oncall
      app.kubernetes.io/component: web
  template:
    metadata:
      labels:
        app.kubernetes.io/name: oncall
        app.kubernetes.io/component: web
    spec:
      containers:
        - name: oncall
          image: knucksie/sre-ab-oncall:0.1
          env:
            - name: DOCKER_DB_BOOTSTRAP
              value: "1"
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8080
          livenessProbe:
            httpGet:
              path: /healthcheck
              port: 8080
            failureThreshold: 1
            initialDelaySeconds: 15
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /healthcheck
              port: 8080
            failureThreshold: 1
            initialDelaySeconds: 15
            periodSeconds: 10
          startupProbe:
            httpGet:
              path: /healthcheck
              port: 8080
            failureThreshold: 10
            initialDelaySeconds: 30
            periodSeconds: 10
          volumeMounts:
            - name: oncall-config
              mountPath: /home/oncall/config/config.yaml
              subPath: oncall.conf
              readOnly: true
      volumes:
        - name: oncall-config
          configMap:
            name: oncall
```
1. Отсутствие ресурсных ограничений позволяет одному приложению пользоваться всеми ресурсами. Необходимо указывать ресурсные ограничения.

### ingress.yaml
```
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: oncall-ingress
  namespace: st-ab5-statsenko
  annotations:
    nginx.ingress.kubernetes.io/use-regex: "true"
spec:
  ingressClassName: nginx
  rules:
    - host: oncall.st-ab5-statsenko.ingress.sre-ab.ru
      http:
        paths:
          - path: "/"
            pathType: Prefix
            backend:
              service:
                name: oncall
                port:
                  number: 8080
```
1. Аннотация use-regex = true, но в манифесте отсутствуют регулярные выражения. Ее следует убрать.
2. Отсутствуют таймауты и лимиты (например, proxy-connect-timeout, proxy-read-timeout, proxy-send-timeout, proxy-body-size). Следует прописывать их в аннотациях для Ingress манифеста