-- Priority view: joins companies + scores + enrichment + outreach + signal aggregates.
create or replace view public.v_priority_leads as
select
    c.cnpj,
    c.razao_social,
    c.group_type,
    c.psav_modality,
    c.capital_social,
    c.data_ultima_alteracao,
    c.endereco_uf,
    c.endereco_municipio,
    c.site,
    s.score_total,
    s.tier,
    s.score_breakdown,
    e.ceo_name,
    e.head_compliance_name,
    e.head_legal_name,
    e.linkedin_employee_count,
    e.linkedin_company_url,
    o.status as outreach_status,
    o.owner,
    o.last_touch_at,
    (
        select count(*)
        from public.signals
        where signals.cnpj = c.cnpj
          and signal_date > now() - interval '90 days'
    ) as signals_90d,
    (
        select max(signal_date)
        from public.signals
        where signals.cnpj = c.cnpj
    ) as last_signal_at
from public.companies c
left join public.scores s     on s.cnpj = c.cnpj
left join public.enrichment e on e.cnpj = c.cnpj
left join public.outreach o   on o.cnpj = c.cnpj
where c.archived = false
order by s.score_total desc nulls last;
