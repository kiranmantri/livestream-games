-- Settings the admin can change online (first one: how many players may join from one IP address).
CREATE TABLE app_settings (
    key        text        PRIMARY KEY CHECK (key IN ('max_users_per_ip')),
    value      text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
