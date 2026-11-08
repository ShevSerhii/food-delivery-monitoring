-- Схема умовного сервісу доставки їжі.
-- Скрипт виконується один раз, при першому старті контейнера PostgreSQL.

CREATE TYPE order_status AS ENUM ('created', 'cooking', 'on_the_way', 'delivered', 'cancelled');

CREATE TABLE restaurants (
    id      serial PRIMARY KEY,
    name    text NOT NULL,
    city    text NOT NULL,
    cuisine text NOT NULL
);

CREATE TABLE couriers (
    id        serial PRIMARY KEY,
    full_name text NOT NULL,
    city      text NOT NULL,
    transport text NOT NULL
);

CREATE TABLE customers (
    id            serial PRIMARY KEY,
    full_name     text NOT NULL,
    city          text NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id            uuid PRIMARY KEY,
    customer_id   int NOT NULL REFERENCES customers (id),
    restaurant_id int NOT NULL REFERENCES restaurants (id),
    courier_id    int REFERENCES couriers (id),
    amount        numeric(10, 2) NOT NULL,
    status        order_status NOT NULL DEFAULT 'created',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    delivered_at  timestamptz
);

CREATE INDEX orders_created_at_idx ON orders (created_at);
CREATE INDEX orders_status_idx ON orders (status);

-- історія змін статусів, по одному запису на кожну подію з черги
CREATE TABLE order_events (
    id         bigserial PRIMARY KEY,
    order_id   uuid NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    status     order_status NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX order_events_order_idx ON order_events (order_id);

-- ---------- довідкові дані ----------

INSERT INTO restaurants (name, city, cuisine) VALUES
    ('Вареничня на Подолі', 'Київ',    'українська'),
    ('Піца Везувій',        'Київ',    'італійська'),
    ('Суші Кіото',          'Київ',    'японська'),
    ('Бургерна 42',         'Київ',    'американська'),
    ('Кав''ярня Ратуша',    'Львів',   'європейська'),
    ('Галицька кухня',      'Львів',   'українська'),
    ('Рамен Бар',           'Львів',   'японська'),
    ('Привоз Фіш',          'Одеса',   'морепродукти'),
    ('Тандир',              'Одеса',   'узбецька'),
    ('Піца Порто',          'Одеса',   'італійська'),
    ('Слобожанська хата',   'Харків',  'українська'),
    ('Вок Сіті',            'Харків',  'азійська'),
    ('Шаурма Стар',         'Харків',  'близькосхідна'),
    ('Дніпровські пироги',  'Дніпро',  'українська'),
    ('Стейк Хаус Дніпро',   'Дніпро',  'американська');

INSERT INTO couriers (full_name, city, transport)
SELECT
    (ARRAY['Андрій','Богдан','Віктор','Денис','Олег','Тарас','Ірина','Оксана','Марія','Юлія'])[1 + (g % 10)]
        || ' ' ||
    (ARRAY['Коваль','Мельник','Бондар','Ткаченко','Кравець','Олійник','Лисенко','Гончар'])[1 + (g % 8)],
    (ARRAY['Київ','Львів','Одеса','Харків','Дніпро'])[1 + (g % 5)],
    (ARRAY['велосипед','скутер','авто','пішки'])[1 + (g % 4)]
FROM generate_series(1, 40) AS g;

INSERT INTO customers (full_name, city, registered_at)
SELECT
    (ARRAY['Олександр','Наталія','Сергій','Катерина','Дмитро','Анна','Максим','Софія','Ігор','Вікторія','Роман','Олена'])[1 + floor(random() * 12)::int]
        || ' ' ||
    (ARRAY['Шевчук','Бойко','Кравченко','Поліщук','Савченко','Руденко','Мороз','Левченко','Захарченко','Павленко'])[1 + floor(random() * 10)::int],
    (ARRAY['Київ','Київ','Київ','Львів','Львів','Одеса','Харків','Дніпро'])[1 + floor(random() * 8)::int],
    now() - (random() * interval '365 days')
FROM generate_series(1, 500);
