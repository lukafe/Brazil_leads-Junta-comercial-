"""Official layout of the Receita Federal CNPJ public dump.

Source: https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf

The CSVs are encoded in latin-1, semicolon-separated, no header, values wrapped
in double quotes. Extensions: .EMPRECSV (Empresas), .ESTABELE
(Estabelecimentos), .SOCIOCSV (Sócios).

NOTE: indexes below are 0-based and follow the column order in the CSV.
Do not guess — the canonical reference is the metadata PDF linked above.
"""

from typing import Final

# ---------- Empresas (.EMPRECSV) — 7 columns ----------
EMPRESAS_COLS: Final[dict[str, int]] = {
    "cnpj_basico": 0,
    "razao_social": 1,
    "natureza_juridica": 2,
    "qualificacao_responsavel": 3,
    "capital_social": 4,
    "porte": 5,
    "ente_federativo_responsavel": 6,
}

# ---------- Estabelecimentos (.ESTABELE) — 30 columns ----------
ESTABELE_COLS: Final[dict[str, int]] = {
    "cnpj_basico": 0,
    "cnpj_ordem": 1,
    "cnpj_dv": 2,
    "identificador_matriz_filial": 3,  # 1 = headquarters, 2 = branch
    "nome_fantasia": 4,
    "situacao_cadastral": 5,  # 01 null, 02 active, 03 suspended, 04 inapt, 08 closed
    "data_situacao_cadastral": 6,
    "motivo_situacao_cadastral": 7,
    "nome_cidade_exterior": 8,
    "pais": 9,
    "data_inicio_atividade": 10,
    "cnae_fiscal_principal": 11,
    "cnae_fiscal_secundaria": 12,
    "tipo_logradouro": 13,
    "logradouro": 14,
    "numero": 15,
    "complemento": 16,
    "bairro": 17,
    "cep": 18,
    "uf": 19,
    "municipio": 20,
    "ddd_1": 21,
    "telefone_1": 22,
    "ddd_2": 23,
    "telefone_2": 24,
    "ddd_fax": 25,
    "fax": 26,
    "correio_eletronico": 27,
    "situacao_especial": 28,
    "data_situacao_especial": 29,
}

# ---------- Sócios (.SOCIOCSV) — 11 columns ----------
SOCIOS_COLS: Final[dict[str, int]] = {
    "cnpj_basico": 0,
    "identificador_socio": 1,  # 1 = legal entity, 2 = individual, 3 = foreign
    "nome_socio_ou_razao_social": 2,
    "cnpj_cpf_socio": 3,  # for individuals it comes partially masked: ***123456**
    "qualificacao_socio": 4,
    "data_entrada_sociedade": 5,
    "pais": 6,
    "representante_legal": 7,
    "nome_representante": 8,
    "qualificacao_representante_legal": 9,
    "faixa_etaria": 10,
}


# Cadastral situation codes
SITUACAO_ATIVA = "02"

# Identifiers
MATRIZ = "1"
SOCIO_PF = "2"


# Glob patterns for the files extracted from the dump.
# The Receita publishes each set in ~10 fragments (Empresas0.zip ... Empresas9.zip);
# every zip contains ONE csv with a custom raw extension (no .csv suffix).
EMPRESAS_GLOB = "*.EMPRECSV"
ESTABELE_GLOB = "*.ESTABELE"
SOCIOS_GLOB = "*.SOCIOCSV"
