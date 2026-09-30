# Эталонные примеры UML Sequence

## Содержание

1. Восстановление пароля
2. Интеграция EventTasksGA с Todoist через RabbitMQ

Примеры задают стиль и правила, но не являются источником архитектуры для нового Use Case.
Не переносить из них участников, методы, таблицы, поля или ответы в другой сценарий.

## 1. Восстановление пароля

```plantuml
@startuml
autonumber
actor Пользователь
participant "Веб-приложение" as WebApp
participant Backend
database "База данных (БД)" as DB
participant "Внешняя система Email-рассылок (Unisender)" as EmailSystem

activate Пользователь
Пользователь -> WebApp : Вводит email и нажимает "Восстановить пароль"
activate WebApp

WebApp -> Backend : Восстановить пароль \nPOST /password-recovery [email]
activate Backend

Backend -> DB : Ищет пользователя по email в таблице user
activate DB
DB --> Backend : Данные пользователя
deactivate DB

Backend -> Backend : Генерация токена восстановления пароля \n[ссылка, срок действия]
activate Backend
deactivate Backend

Backend -> DB : Запись токена восстановления в БД
activate DB
DB --> Backend : Подтверждение сохранения
deactivate DB

Backend -> EmailSystem : Отправить email-сообщение \nPOST /sendEmail \n[для кого + тема + тело письма + api-ключ]
activate EmailSystem
EmailSystem --> Backend : Статус отправки
deactivate EmailSystem

Backend --> WebApp : Запрос успешно обработан
deactivate Backend

WebApp --> Пользователь : Показывает сообщение "Инструкция отправлена на email"
deactivate WebApp
deactivate Пользователь
@enduml
```

Почему пример эталонный:

- каждый участник активирован во время работы, включая первого инициатора;
- самовызов Backend имеет вложенный бар;
- запросы сплошные, ответы пунктирные и подписаны результатом;
- БД объявлена как `database`, внешняя система — как `participant`.

## 2. Интеграция EventTasksGA с Todoist через RabbitMQ

### Use Case

Роли пользователей:

- Клиент, заказывающий мероприятие.

Приложения и системы:

- Web-приложение EventTasksGA (Frontend).
- Сервер-приложение EventTasksGA (Backend).
- База данных EventTasksGA.
- RabbitMQ.
- Todoist — внешняя система.

Алгоритм работы:

1. В результате вызова одного из методов `POST /order/webhook` или
   `PATCH /order/{orderId}/status` на Backend EventTasksGA статус заказа обновляется на
   `paid` в БД.
2. Событие «Заказ оплачен» отправляется в соответствующую очередь RabbitMQ. Сообщение
   содержит:
   - `orderId`;
   - `orderNumber`;
   - `client`;
   - `eventTypeCode`;
   - `eventTariffCode`;
   - `paidAt`.
3. Обработчик Backend EventTasksGA получает событие из очереди и запускает интеграционный
   процесс создания проекта и задач по заказу во внешней системе.
4. Обработчик вызывает `POST /orders/{orderId}/projects` для создания проекта в Todoist.
   На вход передаются необходимые параметры названия проекта из сообщения RabbitMQ.
   1. Вызывается метод Todoist `POST https://api.todoist.com/rest/v2/projects`.
   2. Todoist возвращает успешный ответ с идентификатором созданного проекта.
   3. Backend записывает идентификатор в `order.todoist_project_id` БД EventTasksGA.
5. Обработчик вызывает `POST /orders/{orderId}/tasks` для создания стандартного набора задач
   по проекту на организацию мероприятия.
   1. Backend проверяет наличие идентификатора в `order.todoist_project_id`.
   2. Backend получает шаблоны из `todoist_tasks_template` и создаёт стандартный набор задач
      заказа в `todoist_project_task` без внешних идентификаторов.
   3. Для каждой созданной задачи:
      1. Backend вызывает Todoist:
         `POST https://api.todoist.com/rest/v2/tasks` с параметрами из маппинга данных.
      2. Todoist возвращает успешный ответ и идентификатор задачи.
      3. Backend записывает идентификатор в `todoist_project_task.external_id`.
6. Backend записывает в лог сообщение об успешной обработке события по заказу и завершает
   работу обработчика.

Диаграмма ниже сознательно начинается с доставки уже опубликованного события обработчику:
граница этого фрагмента — consumer-процесс.

### Код PlantUML

```plantuml
@startuml
autonumber
participant "Backend EventTasksGA" as Backend
queue "RabbitMQ" as RabbitMQ
participant "Система Todoist" as Todoist
database "База данных EventTasksGA" as Database

RabbitMQ --> Backend : Событие "Заказ оплачен" \n{orderId, orderNumber, client, eventTypeCode, eventTariffCode, paidAt}
activate Backend

== Создание проекта в Todoist ==
Backend -> Backend : Создать проект POST /orders/{orderId}/projects
activate Backend

Backend -> Todoist : Создать проект POST /rest/v2/projects
activate Todoist
Todoist --> Backend : HTTP 200 OK и идентификатор проекта
deactivate Todoist

Backend -> Database : Сохранить идентификатор проекта \nв order.todoist_project_id
activate Database
Database --> Backend : Подтверждение сохранения
deactivate Database

deactivate Backend

== Создание задач по проекту ==
Backend -> Backend : Создать задачи POST /orders/{orderId}/tasks
activate Backend

Backend -> Database : Проверить order.todoist_project_id
activate Database
Database --> Backend : Идентификатор проекта
deactivate Database

Backend -> Database : Получить шаблоны из todoist_tasks_template
activate Database
Database --> Backend : Стандартный набор задач
deactivate Database

Backend -> Database : Создать задачи в todoist_project_task без external_id
activate Database
Database --> Backend : Созданные задачи
deactivate Database

loop Для каждой задачи из набора
    Backend -> Todoist : Создать задачу POST /rest/v2/tasks \n[параметры задачи]
    activate Todoist
    Todoist --> Backend : HTTP 200 OK и идентификатор задачи
    deactivate Todoist

    Backend -> Database : Сохранить идентификатор задачи \nв todoist_project_task.external_id
    activate Database
    Database --> Backend : Подтверждение сохранения
    deactivate Database
end

deactivate Backend

== Завершение обработки ==
Backend -> Backend : Записать в лог успешную обработку события
activate Backend
deactivate Backend

deactivate Backend
@enduml
```

Почему пример эталонный:

- RabbitMQ объявлен как `queue` и никогда не активируется;
- доставка события показана пунктирной стрелкой с данными сообщения;
- внутренние интеграционные методы Backend показаны самовызовами с вложенными барами;
- для каждой задачи используется `loop`;
- идентификаторы проекта и задач сохраняются в конкретные поля, указанные в Use Case;
- отсутствует выдуманное подтверждение обработки в RabbitMQ.
