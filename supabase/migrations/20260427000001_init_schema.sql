-- PSAV Leads initial schema
-- Tables: companies, partners, signals, enrichment, outreach, scores

create extension if not exists "uuid-ossp";
create extension if not exists "pg_trgm";

-- =========================================================================
-- companies (Group A: BCB-authorised institutions; Group B: newly-created SPSAVs)
-- =========================================================================
create table if not exists public.companies (
    cnpj                    text primary key,
    razao_social            text not null,
    nome_fantasia           text,
    capital_social          numeric(18,2),
    data_constituicao       date,
    data_ultima_alteracao   date,
    cnae_principal          text,
    cnae_secundarios        text[],
    endereco_uf             text,
    endereco_municipio      text,
    site                    text,
    email_contato           text,
    telefone                text,

    group_type              text not null check (group_type in ('A','B')),
    psav_modality           text check (psav_modality in ('intermediaria','custodiante','corretora','indefinida')),
    psav_modality_source    text,

    bcb_authorized          boolean default false,
    bcb_authorization_type  text,

    source_added            text not null,
    first_seen_at           timestamptz default now(),
    last_seen_at            timestamptz default now(),
    archived                boolean default false
);

create index if not exists idx_companies_razao_social_trgm
    on public.companies using gin (razao_social gin_trgm_ops);
create index if not exists idx_companies_group_type
    on public.companies(group_type);
create index if not exists idx_companies_data_ultima_alteracao
    on public.companies(data_ultima_alteracao desc);
create index if not exists idx_companies_psav_modality
    on public.companies(psav_modality);
create index if not exists idx_companies_uf
    on public.companies(endereco_uf);

-- =========================================================================
-- partners (corporate partners and officers; CPF is masked for individuals)
-- =========================================================================
create table if not exists public.partners (
    id              uuid primary key default uuid_generate_v4(),
    cnpj            text not null references public.companies(cnpj) on delete cascade,
    nome            text not null,
    qualificacao    text,
    cpf_cnpj        text,
    percent_share   numeric(5,2),
    data_entrada    date,
    created_at      timestamptz default now()
);

create index if not exists idx_partners_cnpj on public.partners(cnpj);
create unique index if not exists uq_partners_cnpj_nome on public.partners(cnpj, nome);

-- =========================================================================
-- signals (per-company event timeline)
-- =========================================================================
create table if not exists public.signals (
    id              uuid primary key default uuid_generate_v4(),
    cnpj            text not null references public.companies(cnpj) on delete cascade,
    signal_type     text not null,
    signal_date     timestamptz not null,
    signal_source   text not null,
    signal_payload  jsonb,
    confidence      numeric(3,2) default 1.0,
    created_at      timestamptz default now()
);

-- accepted signal_type values:
--   razao_social_update, fundraising, security_incident, key_hire,
--   job_opening, public_intent, partnership, regulatory_filing,
--   product_launch, media_mention

create index if not exists idx_signals_cnpj on public.signals(cnpj);
create index if not exists idx_signals_type_date on public.signals(signal_type, signal_date desc);

-- =========================================================================
-- enrichment (one row per CNPJ)
-- =========================================================================
create table if not exists public.enrichment (
    cnpj                        text primary key references public.companies(cnpj) on delete cascade,
    linkedin_company_url        text,
    linkedin_employee_count     int,
    linkedin_followers          int,
    linkedin_industry           text,

    ceo_name                    text,
    ceo_linkedin                text,
    head_compliance_name        text,
    head_compliance_linkedin    text,
    head_legal_name             text,
    head_legal_linkedin         text,
    cto_name                    text,
    cto_linkedin                text,

    auditor_atual               text,
    has_crypto_product_live     boolean default false,
    crypto_product_url          text,
    site_mentions_spsav         boolean default false,

    last_enriched_at            timestamptz default now()
);

-- =========================================================================
-- outreach (sales pipeline status)
-- =========================================================================
create table if not exists public.outreach (
    id              uuid primary key default uuid_generate_v4(),
    cnpj            text not null references public.companies(cnpj) on delete cascade,
    tier            text check (tier in ('estrategico','ativo','long_tail')),
    status          text check (status in (
        'nao_contatado','contatado','em_conversa','reuniao_agendada',
        'proposta_enviada','fechado_ganho','fechado_perdido','dormindo'
    )) default 'nao_contatado',
    owner           text,
    last_touch_at   timestamptz,
    next_action_at  timestamptz,
    notes           text,
    updated_at      timestamptz default now()
);

create index if not exists idx_outreach_status on public.outreach(status);
create index if not exists idx_outreach_tier on public.outreach(tier);
create unique index if not exists uq_outreach_cnpj on public.outreach(cnpj);

-- =========================================================================
-- scores (recomputed periodically)
-- =========================================================================
create table if not exists public.scores (
    cnpj            text primary key references public.companies(cnpj) on delete cascade,
    score_total     int not null,
    score_breakdown jsonb not null,
    tier            text not null,
    computed_at     timestamptz default now()
);

create index if not exists idx_scores_total on public.scores(score_total desc);
