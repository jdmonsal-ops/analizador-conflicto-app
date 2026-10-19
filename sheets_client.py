# sheets_client.py

import gspread
from google.oauth2.service_account import Credentials
import streamlit as st
from typing import List, Dict, Any

# Importamos nuestro motor de enriquecimiento
from data_enricher import enrich_items_for_spreadsheet

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

COLUMNS_ORDER = [
    "CONCA", "_id", "marca_temporal", "evento", "link", "tipo_fuente", "fuente",
    "fecha_hecho", "fecha_publicacion", "Semana", "year", "month", "day",
    "departamento", "id_dpto", "municipio", "id_mpio", "dane2", "vereda",
    "corregimiento", "zona", "descripcion_hecho", "tipo_accion", "responsable",
    "subgrupo-responsable", "num_victm_civiles_heridas", "num_victm_civiles_muertas",
    "num_victm_fzapub_heridas", "num_victm_fzapub_muertas", "num_integrantes_gai_heridos",
    "num_integrantes_gai_muertos", "otros_number", "tipo_revision_prensa", "PDET",
    "PNIS", "Acciones tradicionales", "Accion"
]

def get_gspread_client() -> gspread.Client:
    """Autentica y devuelve el cliente de gspread."""
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    else:
        try:
            credentials = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
        except Exception as e:
            raise FileNotFoundError(
                "No se encontraron credenciales de GCP. Configura st.secrets['gcp_service_account'] "
                f"o añade 'credentials.json' en la raíz. Error: {e}"
            )
    return gspread.authorize(credentials)


def append_items_to_spreadsheet(
    spreadsheet_id: str, 
    raw_items: List[Dict[str, Any]], 
    datos_noticia: Dict[str, Any], 
    worksheet_name: str = "Hoja 1"
) -> int:
    """
    Enriquece los ítems con las 37 columnas y los inserta al final de la hoja.
    """
    if not raw_items:
        return 0

    # 1. Enriquecer las filas (DANE, fechas, PDET, PNIS, Accion, Acciones tradicionales)
    enriched_items = enrich_items_for_spreadsheet(raw_items, datos_noticia)

    client = get_gspread_client()
    sh = client.open_by_key(spreadsheet_id)
    
    try:
        worksheet = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        worksheet = sh.get_worksheet(0)

    # 2. Encabezados si la hoja está vacía
    existing_records = worksheet.get_all_values()
    if not existing_records:
        worksheet.append_row(COLUMNS_ORDER)

    # 3. Preparar filas en orden estricto
    rows_to_insert = [
        [item.get(col, "") for col in COLUMNS_ORDER]
        for item in enriched_items
    ]

    # 4. Inserción en lote interpretando tipos con USER_ENTERED
    worksheet.append_rows(rows_to_insert, value_input_option="USER_ENTERED")

    return len(rows_to_insert)