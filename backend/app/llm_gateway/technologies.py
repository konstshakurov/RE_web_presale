"""Белый список технологий для обезличивания (FR-023, KON-72).

NER (natasha) помечает названия продуктов и технологий (Jira, Kafka, Camunda...) как организации,
и во внешнюю LLM уходит запрос без ключевых навыков. Термины отсюда NER-этап не заменяет.
Список расширяется настройкой ``llm_tech_terms`` (app_setting, JSON-массив строк) без правки кода.
Названия вендоров, совпадающие с названиями компаний (Oracle, Microsoft, Google, SAP, Яндекс),
сюда намеренно не включены: их маскирование безопаснее. Словарь обезличивания из Настроек
сильнее белого списка: термин, добавленный туда, маскируется всегда.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_TERMS = """
java kotlin scala groovy python go golang rust ruby php perl c c++ c# .net dotnet asp.net
javascript js typescript ts node.js nodejs vue vue.js react react.js angular angularjs next.js
nuxt svelte jquery html html5 css css3 sass scss bootstrap tailwind webpack vite
spring springboot hibernate jpa jdbc maven gradle junit testng mockito
django flask fastapi celery pandas numpy pytorch tensorflow scikit-learn
sql nosql plsql pl/sql t-sql tsql postgresql postgres mysql mariadb mssql
clickhouse mongodb cassandra redis elasticsearch elastic opensearch kibana logstash
greenplum hadoop hive spark airflow dbt etl elt dwh olap
kafka rabbitmq activemq mq nats grpc rest restful soap graphql websocket openapi swagger
json xml yaml xsd wsdl xslt csv protobuf avro oauth oauth2 openid keycloak saml jwt ldap
docker kubernetes k8s helm openshift podman nginx haproxy tomcat jboss wildfly weblogic
git github gitlab bitbucket svn jenkins teamcity bamboo ansible terraform linux ubuntu bash
aws azure gcp prometheus grafana zabbix sonarqube nexus artifactory
jira confluence trello redmine youtrack
asana miro figma axure balsamiq draw.io drawio visio xmind sparx
postman soapui jmeter selenium cypress playwright appium allure testrail qase
camunda activiti flowable bpmn dmn cmmn uml erd idef0 idef3 dfd c4 archimate togaf
plantuml mermaid swimlane
powerbi tableau qlik qlikview superset metabase excel word powerpoint sharepoint
1c 1с 1c:enterprise 1с:предприятие 1c:erp 1с:erp 1c:зуп bitrix bitrix24 битрикс битрикс24
salesforce sap hana abap odoo
scrum kanban agile waterfall devops mvp rup
pmbok babok itil cobit
rpa uipath llm gpt rag
""".split("\n")


# составные термины задаются явно: в общем списке выше слова разделены пробелами
_MULTIWORD = (
    "spring boot", "sql server", "ms sql", "oracle db", "jira software", "jira service desk",
    "service desk", "enterprise architect", "power bi", "blue prism", "ibm mq",
    "visual studio", "visual studio code", "microsoft office", "ms office", "ms project",
    "react native", "machine learning", "data science", "big data",
)

_SINGLE = {w for block in _TERMS for w in block.split()}

DEFAULT_TECH_TERMS: frozenset[str] = frozenset(_SINGLE | set(_MULTIWORD))

_QUOTES = str.maketrans("", "", "«»\"“”„'`")
_SPLIT_RE = re.compile(r"[\s,;/|()]+")


def normalize(term: str) -> str:
    return " ".join(term.translate(_QUOTES).split()).strip(" .,;:").lower()


def build_terms(extra: Iterable[str] = ()) -> frozenset[str]:
    """Встроенный список плюс расширение из настроек."""
    out = set(DEFAULT_TECH_TERMS)
    for t in extra:
        n = normalize(str(t))
        if n:
            out.add(n)
    return frozenset(out)


def is_tech(fragment: str, terms: frozenset[str]) -> bool:
    """Фрагмент целиком - технология либо перечисление технологий («Jira, Confluence»)."""
    n = normalize(fragment)
    if not n:
        return False
    if n in terms:
        return True
    words = [w for w in _SPLIT_RE.split(n) if w]
    if not words:
        return False
    # «Spring Boot Kafka»: жадно собираем составные термины из подряд идущих слов
    i = 0
    while i < len(words):
        for j in range(min(len(words), i + 3), i, -1):
            if " ".join(words[i:j]) in terms:
                i = j
                break
        else:
            return False
    return True
