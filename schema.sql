create table if not exists users (
    user_id bigint primary key,
    username text default '',
    first_name text default '',
    created_at timestamptz not null default now()
);

create table if not exists admins (
    user_id bigint primary key references users(user_id) on delete cascade
);

create table if not exists premium (
    user_id bigint primary key references users(user_id) on delete cascade,
    expires_at timestamptz not null
);

create table if not exists banned_users (
    user_id bigint primary key references users(user_id) on delete cascade,
    banned_by bigint not null,
    created_at timestamptz not null default now()
);

create table if not exists files (
    file_id text primary key,
    channel_id bigint not null,
    message_id bigint not null,
    caption text default '',
    created_at timestamptz not null default now(),
    unique(channel_id, message_id)
);

create table if not exists main_links (
    token text primary key,
    target text not null,
    created_at timestamptz not null default now()
);

create table if not exists batches (
    batch_id text primary key,
    created_at timestamptz not null default now()
);

create table if not exists batch_items (
    batch_id text not null references batches(batch_id) on delete cascade,
    file_id text not null references files(file_id) on delete cascade,
    position integer not null,
    primary key(batch_id, file_id)
);

create table if not exists tokens (
    token text primary key,
    user_id bigint not null,
    target text not null,
    expires_at timestamptz not null,
    used boolean not null default false,
    created_at timestamptz not null default now()
);

create table if not exists fsub_channels (
    channel_id text primary key,
    invite_link text default '',
    title text default '',
    created_at timestamptz not null default now()
);

create table if not exists settings (
    key text primary key,
    value text
);
