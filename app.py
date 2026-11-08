import streamlit as st
import streamlit.components.v1 as components
from google import genai
import pandas as pd
from datetime import date, datetime
from PIL import Image
import json
import time
import os
import base64
import hashlib
import tempfile
import requests
import numpy as np
import cv2
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import create_engine, inspect as sa_inspect, text as sa_text, types as sa_types
import snowflake.snowpark as snowpark
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import asyncio
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.instruction import Instruction
from solders.message import MessageV0
from solders.transaction import VersionedTransaction

# ReportLab & OpenPyXL for PDF and Excel exports
try:
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    import openpyxl
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

# _snowflake only exists in the warehouse runtime; local/container uses REST instead.
try:
    import _snowflake
except ImportError:
    _snowflake = None

# Configure the Streamlit page
st.set_page_config(
    page_title="Grease of Mind", 
    page_icon="GreaseOfMindLogo.png",  # Update this string to match your exact file name
    layout="wide"
)

# Snowflake intake schema used by the Sample Intake review
DB_SCHEMA = os.environ.get("SNOWFLAKE_DB_SCHEMA", "MRG_LABS_DB.INTAKE")
SF_DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "MRG_LABS_DB")
SF_SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "INTAKE")
SEMANTIC_VIEW = f"{DB_SCHEMA}.MRG_INTAKE_SV"
CSV_COLS = [
    "SAMPLE_ID", "DATE_SAMPLED", "DATE_RECEIVED", "MACHINE", "COMPONENT",
    "SERIAL_NUMBER", "REFERENCE_LUBE",
]
OCR_TO_SF = {
    "Sample ID": "SAMPLE_ID",
    "Date Sampled": "DATE_SAMPLED",
    "Date Received": "DATE_RECEIVED",
    "Machine": "MACHINE",
    "Component": "COMPONENT",
    "Serial Number": "SERIAL_NUMBER",
    "Reference Lube": "REFERENCE_LUBE",
}

# Inject MRG Labs CSS Theme, Bento Box Layout, and Spinning Loading Circle
st.markdown("""
    <style>
        /* Reduce the top padding of the main Streamlit container */
        .block-container {
            padding-top: 2rem !important;
            padding-bottom: 0rem !important;
        }
        /* MRG Labs Brand Styling */
        :root {
            --mrg-blue: #004B87;
            --mrg-light-blue: #005A9E;
            --mrg-bg-gray: #f8fafc;
            --mrg-border: #e2e8f0;
        }
        h1, h2, h3, h4 {
            color: var(--mrg-blue) !important;
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
        }
        .stButton>button {
            background-color: var(--mrg-blue);
            color: white;
            border-radius: 4px;
            border: 1px solid var(--mrg-blue);
            transition: all 0.3s ease;
        }
        .stButton>button:hover {
            background-color: var(--mrg-light-blue);
            border: 1px solid var(--mrg-light-blue);
            color: white;
        }
        .stDownloadButton>button {
            background-color: #28a745;
            border: 1px solid #28a745;
        }
        .stDownloadButton>button:hover {
            background-color: #218838;
            border: 1px solid #218838;
        }

        /* Hide Streamlit's default uploaded files list with the blank icons */
        [data-testid="stFileUploader"] ul {
            display: none !important;
        }

        /* Inline Status Message Styling */
        .alert-container {
            display: flex;
            justify-content: flex-end;
            align-items: center;
            height: 100%;
            margin-top: 5px;
        }
        .alert-box {
            position: relative;
            padding: 8px 30px 8px 15px;
            background-color: #e6f4ea;
            color: #137333;
            border-radius: 4px;
            border: 1px solid #ceead6;
            font-size: 14px;
        }
        .alert-box .close-btn {
            display: none;
            position: absolute;
            top: 50%;
            right: 8px;
            transform: translateY(-50%);
            cursor: pointer;
            font-size: 12px;
            font-weight: bold;
        }
        .alert-box:hover .close-btn {
            display: block;
        }
        #close-alert { display: none; }
        #close-alert:checked ~ .alert-box { display: none; }

        /* OVERALL: Replace Streamlit default running indicator (running people/man) with a sleek spinning loading circle */
        [data-testid="stStatusWidget"] svg,
        [data-testid="stStatusWidget"] img,
        [data-testid="stStatusWidget"] [data-testid="stIconMaterial"],
        .stStatusWidget svg,
        .stStatusWidget img {
            display: none !important;
        }

        /* Hide all widget contents (Stop button, "Running..." text); keep only the single spinner */
        [data-testid="stStatusWidget"] > *,
        .stStatusWidget > * {
            display: none !important;
        }
        [data-testid="stStatusWidget"],
        .stStatusWidget {
            font-size: 0 !important;
        }

        [data-testid="stStatusWidget"]::before,
        .stStatusWidget::before {
            content: "" !important;
            display: inline-block !important;
            width: 16px !important;
            height: 16px !important;
            border: 2.5px solid rgba(0, 75, 135, 0.25) !important;
            border-top: 2.5px solid #004B87 !important;
            border-radius: 50% !important;
            animation: mrg-spin 0.75s linear infinite !important;
            margin: 0 !important;
            flex-shrink: 0 !important;
        }

        /* Loading indicator: top-center, 2x size */
        [data-testid="stStatusWidget"],
        .stStatusWidget {
            position: fixed !important;
            top: 1rem !important;
            left: 50% !important;
            right: auto !important;
            transform: translateX(-50%) scale(2) !important;
            transform-origin: top center !important;
            z-index: 999999 !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
        }

        @keyframes mrg-spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        /* Minimalist Bento Box SaaS Dashboard Styling */
        .bento-card {
            background-color: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 18px 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.03), 0 1px 2px rgba(0,0,0,0.02);
            transition: all 0.2s ease;
            height: 100%;
        }
        .bento-card:hover {
            border-color: #cbd5e1;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
        }
        .bento-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        .bento-title {
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #64748b;
        }
        .bento-metric {
            font-size: 26px;
            font-weight: 700;
            color: #0f172a;
            letter-spacing: -0.02em;
            line-height: 1.2;
        }
        .bento-subtitle {
            font-size: 12px;
            color: #64748b;
            margin-top: 4px;
        }
        .bento-badge {
            display: inline-flex;
            align-items: center;
            padding: 3px 8px;
            border-radius: 9999px;
            font-size: 11px;
            font-weight: 600;
        }
        .bento-badge-success { background-color: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
        .bento-badge-warning { background-color: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
        .bento-badge-danger  { background-color: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
        .bento-badge-info    { background-color: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }

        .bento-ai-container {
            background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
            border: 1px solid #cbd5e1;
            border-left: 4px solid #004B87;
            border-radius: 12px;
            padding: 20px;
            margin-top: 15px;
            margin-bottom: 15px;
        }
    </style>
""", unsafe_allow_html=True)

st.image("GreaseOfMindLogo.png", width=200)

# Initialize session state variables
if 'uploader_key' not in st.session_state:
    st.session_state.uploader_key = 0
if 'generated_csv' not in st.session_state:
    st.session_state.generated_csv = None
if 'csv_filename' not in st.session_state:
    st.session_state.csv_filename = None
if 'processed_df' not in st.session_state:
    st.session_state.processed_df = None
if 'last_snowflake_batch' not in st.session_state:
    st.session_state.last_snowflake_batch = None
if 'msgs' not in st.session_state:
    st.session_state.msgs = []
if 'ai_grease_analysis' not in st.session_state:
    st.session_state.ai_grease_analysis = None
if 'grok_qc_report' not in st.session_state:
    st.session_state.grok_qc_report = None
if 'grok_qc_error' not in st.session_state:
    st.session_state.grok_qc_error = None
if 'is_processing' not in st.session_state:
    st.session_state.is_processing = False

api_key = os.environ.get("GEMINI_API_KEY")
XAI_API_KEY = os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-3-mini")
GROK_QC_LOG = os.environ.get("GROK_QC_LOG", "grok_qc_log.md")

if not api_key:
    st.error("Server Configuration Error: GEMINI_API_KEY environment variable is not set.")
    st.stop()


def get_image_base64(image):
    """Helper function to convert PIL Image to base64 for custom HTML rendering."""
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()


def safe_parse_dates(series: pd.Series) -> pd.Series:
    """Robust date parsing across all pandas versions."""
    try:
        return pd.to_datetime(series, format='mixed', errors='coerce').dt.date
    except Exception:
        return pd.to_datetime(series, errors='coerce').dt.date


def df_to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Convert DataFrame to Excel (.xlsx) bytes."""
    buffer = BytesIO()
    clean_df = df.copy()
    if "Solana Audit Trail" in clean_df.columns:
        clean_df = clean_df.drop(columns=["Solana Audit Trail"])
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        clean_df.to_excel(writer, index=False, sheet_name='Visible Samples')
    buffer.seek(0)
    return buffer.getvalue()


def df_to_pdf_bytes(df: pd.DataFrame, title="MRG Labs - Visible Grease Samples") -> bytes:
    """Convert DataFrame to formatted PDF report bytes."""
    if not REPORTLAB_AVAILABLE:
        return b"%PDF-1.4 Empty fallback"
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=15,
        rightMargin=15,
        topMargin=20,
        bottomMargin=20
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading2'],
        fontSize=13,
        textColor=colors.HexColor('#004B87'),
        spaceAfter=8
    )
    elements = [
        Paragraph(f"<b>{title}</b> — Visible records: {len(df)}", title_style),
        Spacer(1, 6)
    ]
    
    cell_style = ParagraphStyle(
        'Cell',
        fontSize=7,
        leading=9,
        textColor=colors.HexColor('#1E293B')
    )
    header_style = ParagraphStyle(
        'Header',
        fontSize=8,
        leading=10,
        textColor=colors.whitesmoke,
        fontName='Helvetica-Bold'
    )
    
    export_df = df.copy()
    if "Solana Audit Trail" in export_df.columns:
        export_df = export_df.drop(columns=["Solana Audit Trail"])
        
    headers = [Paragraph(f"<b>{str(col)}</b>", header_style) for col in export_df.columns]
    table_data = [headers]
    
    for _, row in export_df.iterrows():
        row_cells = []
        for val in row:
            text = "" if (pd.isna(val) or val is None) else str(val)
            if len(text) > 75:
                text = text[:72] + "..."
            row_cells.append(Paragraph(text, cell_style))
        table_data.append(row_cells)
        
    num_cols = max(len(export_df.columns), 1)
    col_width = (doc.width) / num_cols
    
    t = Table(table_data, colWidths=[col_width] * num_cols, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#004B87')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#F8FAFC'), colors.white]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(t)
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def call_reasoning_model(client, prompt: str) -> str:
    """Queries Google Gemini high-level reasoning model with fallback capability."""
    preferred_models = [
        os.environ.get("GEMINI_REASONING_MODEL", "gemini-3.7-flash"),
        "gemini-3.5-flash",
        "gemini-flash-latest",
    ]
    models = []
    for m in preferred_models:
        if m and m not in models:
            models.append(m)

    errors = []
    for model_name in models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            if response and response.text:
                return response.text
            errors.append(f"{model_name}: empty response")
        except Exception as e:
            errors.append(f"{model_name}: {e}")
            continue

    raise RuntimeError("All reasoning models failed:\n" + "\n".join(errors))


def normalize_col_name(c: str) -> str:
    return c.strip().upper().replace(" ", "_")


def validate_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validates a DataFrame of grease samples according to MRG Labs rules,
    strictly differentiating between Small and Large tube sizes to avoid incorrect flags.
    """
    df = df.copy()
    col_map = {c: normalize_col_name(c) for c in df.columns}
    std_df = df.rename(columns=col_map).copy()
    
    for c in ["SAMPLE_ID", "DATE_SAMPLED", "DATE_RECEIVED", "MACHINE", "COMPONENT", "SERIAL_NUMBER", "REFERENCE_LUBE"]:
        if c not in std_df.columns:
            std_df[c] = None
    if "TUBE_SIZE" not in std_df.columns:
        std_df["TUBE_SIZE"] = None

    tube_sizes = []
    missing_flags = []
    missing_reasons = []
    bad_date_flags = []
    bad_date_reasons = []
    turnarounds = []
    
    # 1. Determine Tube Size (Small vs Large)
    for idx, row in std_df.iterrows():
        raw_ts = str(row["TUBE_SIZE"]).strip().lower() if pd.notna(row["TUBE_SIZE"]) else ""
        if "small" in raw_ts or raw_ts == "s":
            t_size = "Small"
        elif "large" in raw_ts or raw_ts == "l":
            t_size = "Large"
        else:
            sn = str(row["SERIAL_NUMBER"]).strip() if pd.notna(row["SERIAL_NUMBER"]) else ""
            rl = str(row["REFERENCE_LUBE"]).strip() if pd.notna(row["REFERENCE_LUBE"]) else ""
            has_sn = sn not in ["", "None", "nan", "NaT", "<NA>", "ERROR"]
            has_rl = rl not in ["", "None", "nan", "NaT", "<NA>", "ERROR"]
            
            if has_sn and not has_rl:
                t_size = "Small"
            elif has_rl and not has_sn:
                t_size = "Large"
            else:
                comp = str(row["COMPONENT"]).strip().upper() if pd.notna(row["COMPONENT"]) else ""
                if "GS" in comp or "NGS" in comp:
                    t_size = "Small"
                else:
                    t_size = "Large"
        tube_sizes.append(t_size)
    std_df["TUBE_SIZE"] = tube_sizes
    
    # 2. Check Duplicate Sample IDs
    valid_ids = std_df["SAMPLE_ID"].dropna().astype(str).str.strip()
    valid_ids = valid_ids[~valid_ids.isin(["", "None", "nan", "NaT", "ERROR"])]
    dup_counts = valid_ids.value_counts()
    dup_id_set = set(dup_counts[dup_counts > 1].index)
    
    is_duplicate = []
    for idx, row in std_df.iterrows():
        sid = str(row["SAMPLE_ID"]).strip() if pd.notna(row["SAMPLE_ID"]) else ""
        is_dup = sid in dup_id_set
        is_duplicate.append(is_dup)
    std_df["IS_DUPLICATE"] = is_duplicate
    
    # 3. Validate Required Fields & Dates based on Tube Size
    today = date.today()
    for idx, row in std_df.iterrows():
        t_size = row["TUBE_SIZE"]
        m_list = []
        d_reasons = []
        
        # Universal required fields
        sid = str(row["SAMPLE_ID"]).strip() if pd.notna(row["SAMPLE_ID"]) else ""
        if not sid or sid in ["None", "nan", "NaT", "<NA>", "ERROR"]:
            m_list.append("Sample ID")
            
        mach = str(row["MACHINE"]).strip() if pd.notna(row["MACHINE"]) else ""
        if not mach or mach in ["None", "nan", "NaT", "<NA>"]:
            m_list.append("Machine")
            
        comp = str(row["COMPONENT"]).strip() if pd.notna(row["COMPONENT"]) else ""
        if not comp or comp in ["None", "nan", "NaT", "<NA>"]:
            m_list.append("Component")
            
        # Field differentiation based on tube size
        if t_size == "Small":
            # Small tube: requires Date Sampled, Serial Number; Reference Lube is omitted
            ds = str(row["DATE_SAMPLED"]).strip() if pd.notna(row["DATE_SAMPLED"]) else ""
            if not ds or ds in ["None", "nan", "NaT", "<NA>"]:
                m_list.append("Date Sampled")
                
            sn = str(row["SERIAL_NUMBER"]).strip() if pd.notna(row["SERIAL_NUMBER"]) else ""
            if not sn or sn in ["None", "nan", "NaT", "<NA>"]:
                m_list.append("Serial Number")
        else:
            # Large tube: requires Reference Lube; Serial Number is omitted
            rl = str(row["REFERENCE_LUBE"]).strip() if pd.notna(row["REFERENCE_LUBE"]) else ""
            if not rl or rl in ["None", "nan", "NaT", "<NA>"]:
                m_list.append("Reference Lube")
                
        # Dates validation & turnaround
        d_samp = None
        d_recv = None
        
        ds_val = row["DATE_SAMPLED"]
        if pd.notna(ds_val) and str(ds_val).strip() not in ["", "None", "nan", "NaT"]:
            try:
                parsed_s = safe_parse_dates(pd.Series([ds_val])).iloc[0]
                if pd.notna(parsed_s):
                    d_samp = parsed_s
                    if d_samp > today:
                        d_reasons.append("Sampled date in future")
                    elif d_samp < date(2000, 1, 1):
                        d_reasons.append("Sampled date before 2000")
                else:
                    d_reasons.append("Invalid Date Sampled format")
            except Exception:
                d_reasons.append("Invalid Date Sampled format")
                
        dr_val = row["DATE_RECEIVED"]
        if pd.notna(dr_val) and str(dr_val).strip() not in ["", "None", "nan", "NaT"]:
            try:
                parsed_r = safe_parse_dates(pd.Series([dr_val])).iloc[0]
                if pd.notna(parsed_r):
                    d_recv = parsed_r
                    if d_recv > today:
                        d_reasons.append("Received date in future")
                else:
                    d_reasons.append("Invalid Date Received format")
            except Exception:
                d_reasons.append("Invalid Date Received format")
                
        if d_samp and d_recv:
            turnaround_days = (d_recv - d_samp).days
            if turnaround_days < 0:
                d_reasons.append("Sampled date is after Received date")
                turnarounds.append(None)
            else:
                turnarounds.append(turnaround_days)
        else:
            turnarounds.append(None)
            
        missing_flags.append(len(m_list) > 0)
        missing_reasons.append(", ".join(m_list) if m_list else "")
        bad_date_flags.append(len(d_reasons) > 0)
        bad_date_reasons.append(", ".join(d_reasons) if d_reasons else "")

    std_df["HAS_MISSING_FIELD"] = missing_flags
    std_df["MISSING_REASONS"] = missing_reasons
    std_df["HAS_BAD_DATE"] = bad_date_flags
    std_df["BAD_DATE_REASONS"] = bad_date_reasons
    std_df["TURNAROUND_DAYS"] = turnarounds
    
    std_df["IS_FLAGGED"] = std_df["IS_DUPLICATE"] | std_df["HAS_MISSING_FIELD"] | std_df["HAS_BAD_DATE"]
    
    flag_reasons = []
    for is_dup, m_r, b_r in zip(std_df["IS_DUPLICATE"], std_df["MISSING_REASONS"], std_df["BAD_DATE_REASONS"]):
        parts = []
        if is_dup:
            parts.append("Duplicate ID")
        if m_r:
            parts.append(f"Missing: {m_r}")
        if b_r:
            parts.append(f"Date issue: {b_r}")
        flag_reasons.append("; ".join(parts) if parts else "None")
    std_df["FLAG_REASON"] = flag_reasons
    
    return std_df


def get_styled_dataframe(df_view: pd.DataFrame):
    """Applies soft red highlights strictly to cells that violated rules for their tube size."""
    cols = ["SAMPLE_ID", "TUBE_SIZE", "FLAG_REASON", "DATE_SAMPLED", "DATE_RECEIVED", "MACHINE", "COMPONENT", "SERIAL_NUMBER", "REFERENCE_LUBE", "TURNAROUND_DAYS"]
    existing_cols = [c for c in cols if c in df_view.columns]
    view_sub = df_view[existing_cols].copy()
    
    def highlight_row_cells(df):
        styles = pd.DataFrame('', index=df.index, columns=df.columns)
        red_bg = 'background-color: #fee2e2; color: #991b1b; font-weight: 500;'
        
        for idx in df.index:
            row_data = df_view.loc[idx]
            is_dup = row_data.get("IS_DUPLICATE", False)
            has_bad_date = row_data.get("HAS_BAD_DATE", False)
            t_size = row_data.get("TUBE_SIZE", "Large")
            
            if is_dup and "SAMPLE_ID" in df.columns:
                styles.at[idx, "SAMPLE_ID"] = red_bg
            if has_bad_date and "DATE_SAMPLED" in df.columns:
                styles.at[idx, "DATE_SAMPLED"] = red_bg
                
            for col in ["SAMPLE_ID", "MACHINE", "COMPONENT"]:
                if col in df.columns:
                    v = row_data.get(col)
                    if pd.isna(v) or not str(v).strip() or str(v).strip() in ["None", "nan", "NaT", "<NA>", "ERROR"]:
                        styles.at[idx, col] = red_bg
                        
            if t_size == "Small":
                for col in ["DATE_SAMPLED", "SERIAL_NUMBER"]:
                    if col in df.columns:
                        v = row_data.get(col)
                        if pd.isna(v) or not str(v).strip() or str(v).strip() in ["None", "nan", "NaT", "<NA>"]:
                            styles.at[idx, col] = red_bg
            else:
                if "REFERENCE_LUBE" in df.columns:
                    v = row_data.get("REFERENCE_LUBE")
                    if pd.isna(v) or not str(v).strip() or str(v).strip() in ["None", "nan", "NaT", "<NA>"]:
                        styles.at[idx, "REFERENCE_LUBE"] = red_bg
                        
        return styles

    return view_sub.style.apply(highlight_row_cells, axis=None)



# ---------- Tab 3: cell-level flags, manual edits, Tiger Data write-back ----------
GRID_HTML = r'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root { --brand:#004B87; --line:#e2e8f0; --bg:#fff; --head:#f1f5f9; --txt:#0f172a; --mut:#64748b;
          --red-bg:#fee2e2; --red-tx:#991b1b; }
  * { box-sizing:border-box; }
  html, body { margin:0; padding:0; font-family:"Source Sans Pro", system-ui, -apple-system, "Segoe UI", sans-serif;
               font-size:13px; color:var(--txt); background:transparent; }
  #wrap { display:flex; flex-direction:column; height:100vh; }
  #hint { color:var(--mut); font-size:12px; padding:2px 2px 6px; }
  #scroller { flex:1; overflow:auto; border:1px solid var(--line); border-radius:8px; background:var(--bg); }
  table { border-collapse:separate; border-spacing:0; width:100%; }
  th { position:sticky; top:0; z-index:2; background:var(--head); text-align:left; font-weight:600;
       padding:7px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }
  td { padding:6px 10px; border-bottom:1px solid #f1f5f9; white-space:nowrap; max-width:320px;
       overflow:hidden; text-overflow:ellipsis; cursor:default; height:31px; }
  td.ro { color:#475569; }
  td.flag { background:var(--red-bg); color:var(--red-tx); font-weight:500; }
  td.edited { box-shadow:inset 3px 0 0 var(--brand); font-style:italic; }
  td.flagchg { outline:2px dashed #f59e0b; outline-offset:-2px; }
  td.editing { padding:0; }
  td input { width:100%; height:30px; border:2px solid var(--brand); outline:none; padding:0 8px;
             font:inherit; background:#fff; color:var(--txt); }
  tr:hover td:not(.flag) { background:#f8fafc; }
  #menu { position:fixed; display:none; z-index:50; min-width:170px; background:#fff; border:1px solid var(--line);
          border-radius:8px; box-shadow:0 8px 24px rgba(15,23,42,.18); padding:4px; }
  #menu div { padding:7px 12px; border-radius:5px; cursor:pointer; user-select:none; }
  #menu div:hover { background:#eff6ff; }
  #menu div.dis { color:#94a3b8; cursor:not-allowed; background:none; }
  #foot { display:flex; justify-content:space-between; align-items:center; height:48px; padding-top:8px; }
  #pending { color:var(--mut); font-size:12px; visibility:hidden; }
  #btns { display:flex; gap:8px; visibility:hidden; }
  #foot.dirty #pending, #foot.dirty #btns { visibility:visible; }
  button { font:inherit; font-weight:600; border-radius:6px; padding:7px 18px; cursor:pointer; border:1px solid var(--line); }
  #cancel { background:#fff; color:var(--txt); }
  #cancel:hover { background:#f1f5f9; }
  #save { background:var(--brand); color:#fff; border-color:var(--brand); }
  #save:hover { filter:brightness(1.1); }
  button:disabled { opacity:.55; cursor:wait; }
</style>
</head>
<body>
<div id="wrap">
  <div id="hint">Right-click a cell to edit, flag, or unflag it. Double-click also edits.</div>
  <div id="scroller"><table id="tbl"></table></div>
  <div id="foot">
    <span id="pending"></span>
    <span id="btns">
      <button id="cancel">Cancel</button>
      <button id="save">Save</button>
    </span>
  </div>
</div>
<div id="menu"></div>

<script>
(function () {
  // ---- Streamlit component protocol (no build step needed) ----
  function send(type, data) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type: type }, data), "*");
  }
  var S = { rows: [], columns: [], editable: [], flags: {}, version: null };
  var pendingEdits = {};   // "row|col" -> {row, col, value}
  var pendingFlags = {};   // "row|col" -> desired boolean (only when differs from original)
  var saving = false, saveTimer = null, ctx = null;

  var $tbl = document.getElementById("tbl"), $menu = document.getElementById("menu"),
      $foot = document.getElementById("foot"), $pending = document.getElementById("pending");

  var key = function (r, c) { return r + "|" + c; };
  var origVal = function (row, c) { var v = row[c]; return v == null ? "" : String(v); };
  var origFlag = function (r, c) { return !!(S.flags[r] && S.flags[r][c]); };
  var curFlag = function (r, c) { var k = key(r, c); return k in pendingFlags ? pendingFlags[k] : origFlag(r, c); };

  function dirtyCount() { return Object.keys(pendingEdits).length + Object.keys(pendingFlags).length; }

  function render() {
    var h = "<thead><tr>" + S.columns.map(function (c) {
      return "<th>" + esc(c.replace(/_/g, " ")) + "</th>";
    }).join("") + "</tr></thead><tbody>";
    S.rows.forEach(function (row) {
      var r = row._i;
      h += "<tr>";
      S.columns.forEach(function (c) {
        var k = key(r, c), cls = [], tip = "";
        var edited = k in pendingEdits;
        var val = edited ? pendingEdits[k].value : origVal(row, c);
        if (S.editable.indexOf(c) < 0) cls.push("ro");
        if (curFlag(r, c)) {
          cls.push("flag");
          tip = (S.flags[r] && S.flags[r][c]) || "Manually flagged";
        }
        if (edited) cls.push("edited");
        if (k in pendingFlags) cls.push("flagchg");
        h += '<td class="' + cls.join(" ") + '" data-r="' + r + '" data-c="' + c + '"' +
             (tip ? ' title="' + esc(tip) + '"' : "") + ">" + esc(val) + "</td>";
      });
      h += "</tr>";
    });
    $tbl.innerHTML = h + "</tbody>";

    var n = dirtyCount();
    $foot.className = n ? "dirty" : "";
    $pending.textContent = n + " unsaved change" + (n === 1 ? "" : "s");
    document.getElementById("save").disabled = saving;
    document.getElementById("cancel").disabled = saving;
    document.getElementById("save").textContent = saving ? "Saving…" : "Save";
    var want = Math.min(S.rows.length * 31 + 38, 520) + 30 + 48 + 14;
    send("streamlit:setFrameHeight", { height: want });
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (m) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m];
    });
  }

  // ---- editing ----
  function startEdit(td) {
    var r = +td.dataset.r, c = td.dataset.c;
    if (S.editable.indexOf(c) < 0) return;
    var row = S.rows.filter(function (x) { return x._i === r; })[0];
    var k = key(r, c);
    var start = k in pendingEdits ? pendingEdits[k].value : origVal(row, c);
    td.className = "editing";
    td.innerHTML = '<input type="text" value="' + esc(start) + '">';
    var inp = td.firstChild; inp.focus(); inp.select();
    var done = false;
    function commit(save) {
      if (done) return; done = true;
      if (save) {
        var v = inp.value.trim();
        if (v === origVal(row, c).trim()) delete pendingEdits[k];
        else pendingEdits[k] = { row: r, col: c, value: v };
      }
      render();
    }
    inp.addEventListener("keydown", function (e) {
      if (e.key === "Enter") commit(true);
      else if (e.key === "Escape") commit(false);
    });
    inp.addEventListener("blur", function () { commit(true); });
  }

  function toggleFlag(r, c) {
    var k = key(r, c), want = !curFlag(r, c);
    if (want === origFlag(r, c)) delete pendingFlags[k]; else pendingFlags[k] = want;
    render();
  }

  // ---- context menu ----
  function openMenu(e, td) {
    e.preventDefault();
    var r = +td.dataset.r, c = td.dataset.c, k = key(r, c);
    var canEdit = S.editable.indexOf(c) >= 0, flagged = curFlag(r, c);
    var items = [
      { label: "✏️  Edit cell", on: canEdit, fn: function () { startEdit(td); } },
      { label: flagged ? "⚐  Unflag cell" : "⚑  Flag cell", on: true, fn: function () { toggleFlag(r, c); } }
    ];
    if (k in pendingEdits || k in pendingFlags) {
      items.push({ label: "↩  Revert this cell", on: true, fn: function () {
        delete pendingEdits[k]; delete pendingFlags[k]; render(); } });
    }
    $menu.innerHTML = "";
    items.forEach(function (it) {
      var d = document.createElement("div");
      d.textContent = it.label;
      if (!it.on) d.className = "dis";
      d.addEventListener("click", function (ev) {
        ev.stopPropagation(); if (!it.on) return; hideMenu(); it.fn();
      });
      $menu.appendChild(d);
    });
    $menu.style.display = "block";
    var w = $menu.offsetWidth, hgt = $menu.offsetHeight;
    $menu.style.left = Math.max(0, Math.min(e.clientX, window.innerWidth - w - 4)) + "px";
    $menu.style.top = Math.max(0, Math.min(e.clientY, window.innerHeight - hgt - 4)) + "px";
  }
  function hideMenu() { $menu.style.display = "none"; }

  document.addEventListener("click", hideMenu);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hideMenu(); });
  document.getElementById("scroller").addEventListener("scroll", hideMenu);
  $tbl.addEventListener("contextmenu", function (e) {
    var td = e.target.closest("td"); if (td && td.dataset.r !== undefined) openMenu(e, td);
  });
  $tbl.addEventListener("dblclick", function (e) {
    var td = e.target.closest("td"); if (td && td.dataset.r !== undefined) startEdit(td);
  });

  // ---- save / cancel ----
  document.getElementById("cancel").addEventListener("click", function () {
    pendingEdits = {}; pendingFlags = {}; render();   // back to exactly how it was
  });
  document.getElementById("save").addEventListener("click", function () {
    if (saving || !dirtyCount()) return;
    saving = true; render();
    send("streamlit:setComponentValue", { dataType: "json", value: {
      action: "save",
      token: Date.now() + "-" + Math.random().toString(36).slice(2),
      version: S.version,
      edits: Object.keys(pendingEdits).map(function (k) { return pendingEdits[k]; }),
      flags: Object.keys(pendingFlags).map(function (k) {
        var p = k.split("|"); return { row: +p[0], col: p.slice(1).join("|"), flagged: pendingFlags[k] };
      })
    }});
    clearTimeout(saveTimer);
    // If Python rejects the save (error), version never changes: unlock the buttons.
    saveTimer = setTimeout(function () { saving = false; render(); }, 8000);
  });

  // ---- receive data from Python ----
  window.addEventListener("message", function (e) {
    if (!e.data || e.data.type !== "streamlit:render") return;
    var a = e.data.args || {};
    var newVersion = a.version;
    if (S.version !== null && newVersion !== S.version) {   // dataset changed or save succeeded
      pendingEdits = {}; pendingFlags = {}; saving = false; clearTimeout(saveTimer);
    }
    S.rows = a.rows || []; S.columns = a.columns || []; S.editable = a.editable || [];
    S.flags = a.flags || {}; S.version = newVersion;
    if (!document.activeElement || document.activeElement.tagName !== "INPUT") render();
  });

  send("streamlit:componentReady", { apiVersion: 1 });
})();
</script>
</body>
</html>
'''


def _ensure_grid_dir() -> str:
    """Writes the embedded grid component to disk (next to app.py, else the temp dir)."""
    import tempfile
    for base in (os.path.dirname(os.path.abspath(__file__)), tempfile.gettempdir()):
        d = os.path.join(base, "grid_editor")
        try:
            os.makedirs(d, exist_ok=True)
            f = os.path.join(d, "index.html")
            if not os.path.exists(f) or open(f, encoding="utf-8").read() != GRID_HTML:
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write(GRID_HTML)
            return d
        except OSError:
            continue
    raise RuntimeError("Could not create the grid_editor component folder (no writable directory).")


GRID_DIR = _ensure_grid_dir()
grid_editor = components.declare_component("mrg_grid_editor", path=GRID_DIR)

GRID_COLS = ["SAMPLE_ID", "TUBE_SIZE", "FLAG_REASON", "DATE_SAMPLED", "DATE_RECEIVED",
             "MACHINE", "COMPONENT", "SERIAL_NUMBER", "REFERENCE_LUBE", "TURNAROUND_DAYS"]
# Columns stored in Tiger Data lab_samples (normalized names) -> user can edit these
DB_EDIT_COLS = ["SAMPLE_ID", "DATE_SAMPLED", "DATE_RECEIVED", "MACHINE", "COMPONENT",
                "SERIAL_NUMBER", "REFERENCE_LUBE"]
COL_LABELS = {
    "SAMPLE_ID": "Sample ID", "DATE_SAMPLED": "Date Sampled", "DATE_RECEIVED": "Date Received",
    "MACHINE": "Machine", "COMPONENT": "Component", "SERIAL_NUMBER": "Serial Number",
    "REFERENCE_LUBE": "Reference Lube",
}
_BLANK_VALS = {"", "None", "nan", "NaT", "<NA>"}


def _is_blank(v) -> bool:
    return v is None or (not isinstance(v, str) and pd.isna(v)) or str(v).strip() in _BLANK_VALS


def _disp(v) -> str:
    return "" if _is_blank(v) else str(v)


def compute_cell_flags(df: pd.DataFrame) -> dict:
    """Maps each rule violation in a validated df to the cell that caused it.
    Returns {(row_idx, COL): [(kind, text), ...]}; kind in dup | missing | date | manual."""
    cells = {}

    def add(i, col, kind, text):
        cells.setdefault((i, col), []).append((kind, text))

    for i, row in df.iterrows():
        if bool(row.get("IS_DUPLICATE", False)):
            add(i, "SAMPLE_ID", "dup", "Duplicate ID")
        sid = row.get("SAMPLE_ID")
        if _is_blank(sid) or str(sid).strip() == "ERROR":
            add(i, "SAMPLE_ID", "missing", COL_LABELS["SAMPLE_ID"])
        for col in ("MACHINE", "COMPONENT"):
            if _is_blank(row.get(col)):
                add(i, col, "missing", COL_LABELS[col])
        if row.get("TUBE_SIZE") == "Small":
            for col in ("DATE_SAMPLED", "SERIAL_NUMBER"):
                if _is_blank(row.get(col)):
                    add(i, col, "missing", COL_LABELS[col])
        elif _is_blank(row.get("REFERENCE_LUBE")):
            add(i, "REFERENCE_LUBE", "missing", COL_LABELS["REFERENCE_LUBE"])
        for reason in [r.strip() for r in str(row.get("BAD_DATE_REASONS", "") or "").split(",") if r.strip()]:
            if "after Received" in reason:
                add(i, "DATE_SAMPLED", "date", reason)
                add(i, "DATE_RECEIVED", "date", reason)
            elif "Sampled" in reason:
                add(i, "DATE_SAMPLED", "date", reason)
            elif "Received" in reason:
                add(i, "DATE_RECEIVED", "date", reason)
    return cells


def apply_flag_overrides(data: pd.DataFrame, overrides: dict):
    """Applies manual flag/unflag overrides, then recomputes row-level flag columns.
    Returns (data, effective_cells)."""
    auto = compute_cell_flags(data)
    eff = {k: list(v) for k, v in auto.items()}
    for (i, col), forced in overrides.items():
        if forced:
            eff.setdefault((i, col), [("manual", "Manually flagged")])
        else:
            eff.pop((i, col), None)
    if not overrides:
        return data, eff

    data = data.copy()
    touched = {i for (i, _c) in overrides}
    for i in touched:
        if i not in data.index:
            continue
        row_items = [(c, it) for (ri, c), items in eff.items() if ri == i for it in items]
        dup = any(k == "dup" for _c, (k, _t) in row_items)
        missing = [t for _c, (k, t) in row_items if k == "missing"]
        dates = list(dict.fromkeys(t for _c, (k, t) in row_items if k == "date"))
        manual = [COL_LABELS.get(c, c) for c, (k, _t) in row_items if k == "manual"]
        data.at[i, "IS_DUPLICATE"] = dup
        data.at[i, "HAS_MISSING_FIELD"] = bool(missing)
        data.at[i, "MISSING_REASONS"] = ", ".join(missing)
        data.at[i, "HAS_BAD_DATE"] = bool(dates)
        data.at[i, "BAD_DATE_REASONS"] = ", ".join(dates)
        parts = []
        if dup:
            parts.append("Duplicate ID")
        if missing:
            parts.append("Missing: " + ", ".join(missing))
        if dates:
            parts.append("Date issue: " + ", ".join(dates))
        if manual:
            parts.append("Manually flagged: " + ", ".join(manual))
        data.at[i, "FLAG_REASON"] = "; ".join(parts) if parts else "None"
        data.at[i, "IS_FLAGGED"] = bool(row_items)
    return data, eff


def tab3_signature(df: pd.DataFrame) -> str:
    return hashlib.md5(pd.util.hash_pandas_object(df.astype(str), index=False).values.tobytes()).hexdigest()


def get_tab3_state(sig: str) -> dict:
    """Session-held edits + flag overrides for the dataset currently loaded in tab 3."""
    s = st.session_state.get("tab3_edit_state")
    if not s or s["sig"] != sig:
        s = {"sig": sig, "edits": {}, "flags": {}, "version": 0, "handled": None}
        st.session_state["tab3_edit_state"] = s
    return s


def normalize_raw(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns=normalize_col_name).reset_index(drop=True).astype(object)


def apply_edit_overlay(raw_norm: pd.DataFrame, edits: dict) -> pd.DataFrame:
    out = raw_norm.copy()
    for (r, col), val in edits.items():
        if col not in out.columns:
            out[col] = None
        if r in out.index:
            out.at[r, col] = val
    return out


def _coerce_for_db(val, sa_type, strict=True):
    """strict=True for new values (bad date raises); strict=False for old values used in WHERE
    (an unparseable old date was stored as NULL by tab 1, so it matches NULL)."""
    s = "" if val is None else str(val).strip()
    if s in _BLANK_VALS:
        return None
    if isinstance(sa_type, (sa_types.Date, sa_types.DateTime)):
        parsed = safe_parse_dates(pd.Series([s])).iloc[0]
        if pd.isna(parsed):
            if strict:
                raise ValueError(f"'{s}' is not a valid date (use YYYY-MM-DD)")
            return None
        return parsed
    return s


def update_lab_samples(engine, changes: list):
    """changes: [{'old': {COL: v}, 'new': {COL: v}}]. One UPDATE per row, in one transaction.
    Returns number of changes that matched no DB row."""
    db_cols = {normalize_col_name(c["name"]): c for c in sa_inspect(engine).get_columns("lab_samples")}
    q = engine.dialect.identifier_preparer.quote
    unmatched = 0
    with engine.begin() as conn:
        for ch in changes:
            sets, wheres, params = [], [], {}
            for n, col in enumerate(DB_EDIT_COLS):
                if col not in db_cols:
                    continue
                name, typ = q(db_cols[col]["name"]), db_cols[col]["type"]
                params[f"o{n}"] = _coerce_for_db(ch["old"].get(col), typ, strict=False)
                if isinstance(typ, (sa_types.Date, sa_types.DateTime)):
                    wheres.append(f"{name} IS NOT DISTINCT FROM :o{n}")
                else:
                    wheres.append(f"NULLIF(BTRIM(CAST({name} AS TEXT)), '') IS NOT DISTINCT FROM :o{n}")
                if _disp(ch["old"].get(col)) != _disp(ch["new"].get(col)):
                    params[f"n{n}"] = _coerce_for_db(ch["new"].get(col), typ)
                    sets.append(f"{name} = :n{n}")
            if not sets:
                continue
            res = conn.execute(
                sa_text(f"UPDATE lab_samples SET {', '.join(sets)} WHERE {' AND '.join(wheres)}"), params
            )
            if res.rowcount == 0:
                unmatched += 1
    return unmatched


def save_grid_changes(payload: dict, raw_base: pd.DataFrame, state: dict):
    """Persists grid edits to Tiger Data and applies flag overrides. Returns (level, message)."""
    edits = payload.get("edits") or []
    flag_changes = payload.get("flags") or []
    by_row = {}
    for e in edits:
        by_row.setdefault(int(e["row"]), {})[e["col"]] = str(e["value"]).strip()

    unmatched = 0
    if by_row:
        db_uri = os.environ.get("TIGERDATA_URI")
        if not db_uri:
            return "error", "TIGERDATA_URI is not set. Nothing was saved."
        current = apply_edit_overlay(raw_base, state["edits"])
        changes = []
        for r, newvals in by_row.items():
            old = {c: (current.at[r, c] if c in current.columns else None) for c in DB_EDIT_COLS}
            new = dict(old)
            new.update(newvals)
            changes.append({"old": old, "new": new})
        try:
            unmatched = update_lab_samples(create_engine(db_uri), changes)
        except Exception as ex:
            return "error", f"Save failed, nothing was written: {ex}"
        for r, newvals in by_row.items():
            for c, v in newvals.items():
                state["edits"][(r, c)] = v
                state["flags"].pop((r, c), None)   # let validation re-decide an edited cell

    # Flag changes are relative to the auto flags AFTER edits
    if flag_changes:
        validated = validate_dataframe(apply_edit_overlay(raw_base, state["edits"]))
        auto = compute_cell_flags(validated)
        for fc in flag_changes:
            k = (int(fc["row"]), fc["col"])
            if bool(fc["flagged"]) == (k in auto):
                state["flags"].pop(k, None)
            else:
                state["flags"][k] = bool(fc["flagged"])

    bits = []
    if by_row:
        bits.append(f"{len(edits)} edit(s) saved to Tiger Data")
    if flag_changes:
        bits.append(f"{len(flag_changes)} flag change(s) applied")
    msg = ", ".join(bits) + "."
    if unmatched:
        return "warning", msg + f" {unmatched} row(s) not found in lab_samples, so the database was not updated for them."
    return "success", msg


def generate_ai_grease_analysis(client, validated_df: pd.DataFrame) -> str:
    """Generates deep tribological and grease pattern intelligence using high-level reasoning model."""
    total_samples = len(validated_df)
    flagged_count = int(validated_df["IS_FLAGGED"].sum())
    clean_count = total_samples - flagged_count
    small_count = int((validated_df["TUBE_SIZE"] == "Small").sum())
    large_count = int((validated_df["TUBE_SIZE"] == "Large").sum())
    
    top_machines = validated_df["MACHINE"].dropna()[~validated_df["MACHINE"].dropna().isin(["", "None", "nan"])].value_counts().head(8).to_dict()
    top_components = validated_df["COMPONENT"].dropna()[~validated_df["COMPONENT"].dropna().isin(["", "None", "nan"])].value_counts().head(8).to_dict()
    top_lubes = validated_df["REFERENCE_LUBE"].dropna()[~validated_df["REFERENCE_LUBE"].dropna().isin(["", "None", "nan"])].value_counts().head(8).to_dict()
    
    turnaround_series = validated_df["TURNAROUND_DAYS"].dropna()
    avg_turnaround = round(float(turnaround_series.mean()), 1) if not turnaround_series.empty else "N/A"
    max_turnaround = int(turnaround_series.max()) if not turnaround_series.empty else "N/A"
    
    dup_count = int(validated_df["IS_DUPLICATE"].sum())
    missing_count = int(validated_df["HAS_MISSING_FIELD"].sum())
    bad_date_count = int(validated_df["HAS_BAD_DATE"].sum())
    
    flagged_rows = validated_df[validated_df["IS_FLAGGED"]].head(25)
    flagged_summary = []
    for _, r in flagged_rows.iterrows():
        flagged_summary.append({
            "Sample ID": str(r.get("SAMPLE_ID")),
            "Tube Size": str(r.get("TUBE_SIZE")),
            "Machine": str(r.get("MACHINE")),
            "Component": str(r.get("COMPONENT")),
            "Date Sampled": str(r.get("DATE_SAMPLED")),
            "Flag Reason": str(r.get("FLAG_REASON"))
        })

    prompt = f"""You are MRG Laboratories' senior tribologist briefing lab technicians and reliability engineers. They are experts with minutes, not hours. Write for a 10-second glance.

BATCH DATA ({total_samples} grease samples):
- Quality: {clean_count} clean ({round(clean_count/total_samples*100, 1) if total_samples else 0}%), {flagged_count} flagged ({round(flagged_count/total_samples*100, 1) if total_samples else 0}%)
- Tubes: Large {large_count}, Small {small_count}
- Turnaround: avg {avg_turnaround} days, max {max_turnaround} days
- Flags: duplicate IDs {dup_count}, missing fields {missing_count}, bad dates {bad_date_count}
- Top machines: {json.dumps(top_machines)}
- Top components: {json.dumps(top_components)}
- Reference lubes: {json.dumps(top_lubes)}
- Flagged records: {json.dumps(flagged_summary)}

LAB RULES:
- Small tube (RN): needs Sample ID, Date Sampled, Machine, Component (GS/NGS), Serial Number. Reference Lube blank by design.
- Large tube: needs Sample ID, Machine (Equip ID), Component, Reference Lube. Serial Number blank by design.

OUTPUT FORMAT (follow exactly, nothing before or after):

**Bottom line:** one sentence, max 15 words.

### 🚨 Act now
2-4 bullets, max 12 words each. Start with the Sample ID, machine, or number. Only items needing action today.

### 📌 Patterns
Up to 4 bullets, max 12 words each. Format: **Area:** finding + number. Areas: Equipment repeats, Lube mixing risk, Turnaround lag, Label errors. Skip any area with nothing notable.

### ✅ Next steps
Max 3 numbered actions, max 10 words each. Verb first. Owner in brackets: [Lab] or [Field].

STYLE RULES:
- Whole answer under 150 words. Shorter is better.
- Fragments are fine. No intro, outro, hedging, or definitions. Do not restate the data above unless it backs a finding.
- Bold key numbers and IDs. Numbers beat adjectives.
- Use only Sample IDs, machines, and figures present in the data above. Never invent. Mark inference with "likely".
- Nothing notable in a section: write "None." and move on."""

    return call_reasoning_model(client, prompt)


def ask_reasoning_chat(client, messages, validated_df: pd.DataFrame) -> str:
    """Interactive chatbot powered by high-level reasoning model over the current dataset."""
    total_samples = len(validated_df)
    flagged_count = int(validated_df["IS_FLAGGED"].sum())
    
    top_machines = validated_df["MACHINE"].dropna()[~validated_df["MACHINE"].dropna().isin(["", "None", "nan"])].value_counts().head(10).to_dict()
    top_comps = validated_df["COMPONENT"].dropna()[~validated_df["COMPONENT"].dropna().isin(["", "None", "nan"])].value_counts().head(10).to_dict()
    top_lubes = validated_df["REFERENCE_LUBE"].dropna()[~validated_df["REFERENCE_LUBE"].dropna().isin(["", "None", "nan"])].value_counts().head(10).to_dict()
    tube_dist = validated_df["TUBE_SIZE"].value_counts().to_dict()
    
    sample_records = validated_df[["SAMPLE_ID", "TUBE_SIZE", "DATE_SAMPLED", "DATE_RECEIVED", "MACHINE", "COMPONENT", "SERIAL_NUMBER", "REFERENCE_LUBE", "TURNAROUND_DAYS", "IS_FLAGGED", "FLAG_REASON"]].head(150).to_dict(orient="records")
    
    context_header = f"""You are the MRG Laboratories Grease Intelligence AI, a high-level reasoning model with deep expertise in tribology, grease analysis, machine lubrication reliability, and laboratory data auditing.
You are directly analyzing the currently active batch of {total_samples} grease samples ({flagged_count} flagged for data review).

DATASET OVERVIEW:
- Total Samples: {total_samples}
- Tube Size Distribution: {json.dumps(tube_dist)}
- Top Machines: {json.dumps(top_machines)}
- Top Components: {json.dumps(top_comps)}
- Reference Lubes: {json.dumps(top_lubes)}
- Data Rules:
  * Small tubes (RN): Require Sample ID, Date Sampled, Machine, Component (GS/NGS), Serial Number. Reference Lube is deliberately blank.
  * Large tubes: Require Sample ID, Machine, Component, Reference Lube. Serial Number is deliberately blank.

RECORDS SAMPLE (First 150 rows):
{json.dumps(sample_records, default=str)}

RESPONSE STYLE (readers are lab experts with little time):
- Line 1 is the answer. Max 12 words.
- Then at most 4 bullets (max 12 words each) OR one compact table (max 6 rows).
- Under 80 words total, unless the user asks for detail.
- Fragments are fine. No preamble, no restating the question, no closing offer, no hedging, no definitions of terms experts know.
- Bold Sample IDs and numbers. Cite IDs, machines, and figures from the data. Never invent. If the data lacks it, say "Not in data."
- Mark inference with "likely"."""

    conv_lines = []
    for m in messages:
        role_label = "User" if m.get("role") == "user" else "Assistant"
        text_content = ""
        if isinstance(m.get("content"), list):
            text_content = " ".join(c.get("text", "") for c in m["content"] if c.get("type") == "text")
        else:
            text_content = str(m.get("content", ""))
        conv_lines.append(f"{role_label}: {text_content}")

    full_prompt = f"{context_header}\n\nCONVERSATION HISTORY:\n" + "\n".join(conv_lines)
    return call_reasoning_model(client, full_prompt)


def get_snowflake_session():
    """Create a Snowpark session with key-pair auth."""
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "rsa_key.p8")
    with open(key_path, "rb") as key:
        p_key = serialization.load_pem_private_key(
            key.read(),
            password=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "").encode() or None,
            backend=default_backend(),
        )

    pkb = p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    conn_params = {
        "account": os.environ.get("SNOWFLAKE_ACCOUNT"),
        "user": os.environ.get("SNOWFLAKE_USER"),
        "private_key": pkb,
        "role": os.environ.get("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
        "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        "database": SF_DATABASE,
        "schema": SF_SCHEMA,
    }
    missing = [k for k in ("account", "user") if not conn_params.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing Snowflake env vars: {', '.join('SNOWFLAKE_' + m.upper() for m in missing)}"
        )
    return snowpark.Session.builder.configs(conn_params).create()


def ocr_df_to_snowflake_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Map Gemini OCR columns to the Snowflake intake staging schema."""
    sf_df = df.rename(columns=OCR_TO_SF).copy()
    for col in CSV_COLS:
        if col not in sf_df.columns:
            sf_df[col] = None
    if "TUBE_SIZE" not in sf_df.columns:
        sf_df["TUBE_SIZE"] = None
    for col in CSV_COLS + ["TUBE_SIZE"]:
        sf_df[col] = sf_df[col].astype(str).where(sf_df[col].notna(), None)
        sf_df[col] = sf_df[col].replace({"None": None, "nan": None, "NaT": None})
    batch = f"BATCH_{pd.Timestamp.now():%Y%m%d_%H%M%S}"
    sf_df["BATCH_ID"] = batch
    return sf_df[CSV_COLS + ["TUBE_SIZE", "BATCH_ID"]], batch


def push_ocr_batch_to_snowflake(df: pd.DataFrame) -> str:
    """Send the OCR CSV batch into Snowflake STAGING_SAMPLES."""
    session = get_snowflake_session()
    try:
        sf_df, batch = ocr_df_to_snowflake_frame(df)
        session.write_pandas(
            sf_df,
            "STAGING_SAMPLES",
            database=SF_DATABASE,
            schema=SF_SCHEMA,
            auto_create_table=False,
            overwrite=False,
        )
        session.sql(
            f"UPDATE {DB_SCHEMA}.STAGING_SAMPLES SET STATUS = 'PENDING' WHERE STATUS IS NULL"
        ).collect()
        return batch
    finally:
        session.close()


def build_batch_qc_stats(df: pd.DataFrame) -> dict:
    """Lightweight batch stats sent to Grok for automated QC (no raw images)."""
    work = df.copy()
    for col in [
        "Sample ID", "Tube Size", "Date Sampled", "Date Received",
        "Machine", "Component", "Serial Number", "Reference Lube",
    ]:
        if col not in work.columns:
            work[col] = None
    sample_ids = work["Sample ID"].astype(str)
    missing_sample_id = int(
        ((sample_ids == "") | sample_ids.isna() | (sample_ids == "nan") | (sample_ids == "ERROR")).sum()
    )
    dup_ids = sample_ids[sample_ids.duplicated(keep=False) & ~sample_ids.isin(["", "nan", "ERROR", "None"])]
    missing_fields = {
        col: int(work[col].isna().sum() + (work[col].astype(str).str.strip() == "").sum())
        for col in ["Tube Size", "Date Sampled", "Machine", "Component", "Serial Number", "Reference Lube"]
    }
    preview_cols = [c for c in [
        "Sample ID", "Tube Size", "Date Sampled", "Date Received", "Machine",
        "Component", "Serial Number", "Reference Lube",
    ] if c in work.columns]
    return {
        "row_count": int(len(work)),
        "missing_or_error_sample_ids": missing_sample_id,
        "duplicate_sample_id_count": int(dup_ids.nunique()),
        "duplicate_sample_ids": sorted(dup_ids.dropna().astype(str).unique().tolist())[:20],
        "missing_field_counts": missing_fields,
        "preview_rows": work[preview_cols].head(15).fillna("").astype(str).to_dict(orient="records"),
    }


def run_grok_batch_qc(df: pd.DataFrame, batch_id: str | None = None) -> str:
    """Auto QC via xAI Grok. XAI_API_KEY authenticates the chat completions call."""
    if not XAI_API_KEY:
        raise RuntimeError("XAI_API_KEY (or GROK_API_KEY) is not set — cannot run Grok automation.")
    stats = build_batch_qc_stats(df)
    resp = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROK_MODEL,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Grok, the automated QC reviewer for MRG Laboratories sample intake. "
                        "Given batch statistics from OCR → Tiger Data → Snowflake, produce a concise ops report. "
                        "Flag missing entries, duplicate Sample IDs, and tube-size issues. "
                        "End with READY TO SUBMIT or NEEDS REVIEW and 2-4 bullet actions."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Batch ID: {batch_id or 'UNKNOWN'}\n"
                        f"Stats JSON:\n{json.dumps(stats, default=str)}"
                    ),
                },
            ],
        },
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Grok API error {resp.status_code}: {resp.text[:500]}")
    report = resp.json()["choices"][0]["message"]["content"].strip()
    stamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(GROK_QC_LOG, "a", encoding="utf-8") as log:
        log.write(
            f"\n## Grok QC — {stamp}\n"
            f"- Model: `{GROK_MODEL}`\n"
            f"- Batch: `{batch_id or 'UNKNOWN'}`\n"
            f"- Rows: {stats['row_count']}\n\n{report}\n\n---\n"
        )
    return report


def anchor_to_solana(extracted_data):
    """Anchors the sample metadata to Solana Devnet via SPL Memo."""
    async def _send_tx():
        solana_client = AsyncClient("https://api.devnet.solana.com")
        try:
            secret_key_string = os.environ.get("SOLANA_SECRET_KEY")
            if not secret_key_string:
                return "No Solana Wallet Configured"
            
            if secret_key_string.strip().startswith('['):
                try:
                    key_bytes = bytes(json.loads(secret_key_string))
                    wallet = Keypair.from_bytes(key_bytes)
                except Exception as e:
                    return f"Solana TX Failed: Invalid JSON key format - {str(e)}"
            else:
                try:
                    wallet = Keypair.from_base58_string(secret_key_string)
                except Exception as e:
                    return f"Solana TX Failed: Invalid Base58 key format - {str(e)}"
            
            memo_program_id = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")
            
            memo_str = (f"MRG-LAB-ATTEST:{extracted_data.get('Sample ID', 'N/A')}|"
                        f"MACH:{extracted_data.get('Machine', 'N/A')}|"
                        f"DATE:{extracted_data.get('Date Sampled', 'N/A')}")
            
            memo_ix = Instruction(
                program_id=memo_program_id,
                accounts=[], 
                data=memo_str.encode('utf-8')
            )
            
            blockhash_resp = await solana_client.get_latest_blockhash()
            latest_blockhash = blockhash_resp.value.blockhash
            
            msg = MessageV0.try_compile(
                payer=wallet.pubkey(),
                instructions=[memo_ix],
                address_lookup_table_accounts=[],
                recent_blockhash=latest_blockhash
            )
            
            tx = VersionedTransaction(msg, [wallet])
            signature_resp = await solana_client.send_transaction(tx)
            return f"https://explorer.solana.com/tx/{signature_resp.value}?cluster=devnet"
        except Exception as e:
            return f"Solana TX Failed: {str(e)}"
        finally:
            await solana_client.close()

    try:
        return asyncio.run(_send_tx())
    except Exception as e:
        return f"Solana Execution Failed: {str(e)}"


IMAGE_EXTS = ("jpg", "jpeg", "png")
VIDEO_EXTS = ("mp4", "mov", "m4v", "avi", "webm", "mkv")
VIDEO_FRAMES_TO_KEEP = 6      # frames sent to Gemini per video
VIDEO_CANDIDATES_PER_SEG = 6  # frames scored for sharpness per segment


def is_video_file(uploaded_file) -> bool:
    ext = os.path.splitext(uploaded_file.name)[1].lstrip(".").lower()
    return ext in VIDEO_EXTS


def extract_video_frames(uploaded_file, n_keep=VIDEO_FRAMES_TO_KEEP, per_seg=VIDEO_CANDIDATES_PER_SEG):
    """Split video into n_keep time segments; keep the sharpest frame of each.
    Spread across time = spread across the spin = whole label covered.
    Sharpest-pick drops motion-blurred frames."""
    ext = os.path.splitext(uploaded_file.name)[1] or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name
    try:
        cap = cv2.VideoCapture(tmp_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            raise ValueError("Could not read video (unsupported codec?)")

        frames = []
        seg_len = total / n_keep
        for s in range(n_keep):
            start = int(s * seg_len)
            end = max(start + 1, int((s + 1) * seg_len))
            idxs = np.linspace(start, end - 1, per_seg).astype(int)
            best, best_score = None, -1.0
            for i in idxs:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
                ok, frame = cap.read()
                if not ok:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                score = cv2.Laplacian(gray, cv2.CV_64F).var()
                if score > best_score:
                    best, best_score = frame, score
            if best is not None:
                img = Image.fromarray(cv2.cvtColor(best, cv2.COLOR_BGR2RGB))
                img.thumbnail((1024, 1024))
                frames.append(img)
        cap.release()
        if not frames:
            raise ValueError("No frames extracted from video")
        return frames
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def process_label(client, uploaded_file):
    try:
        if is_video_file(uploaded_file):
            images = extract_video_frames(uploaded_file)
            source_note = (
                f"The {len(images)} images are frames, in order, from a video of ONE sample tube "
                "being rotated so the whole label is visible. Each frame shows only part of the label. "
                "Combine information across ALL frames into a single result. Do not return one result per frame."
            )
        else:
            image = Image.open(uploaded_file)
            image.thumbnail((1024, 1024))
            images = [image]
            source_note = "The image shows a sample label."

        prompt = """
        You are an OCR and data extraction assistant for a laboratory.
        """ + source_note + """
        Determine if it is a 'Large' or 'Small' sample label based on the fields present.

        Extract the data to match these exact rules:
        If Large:
        - Tube Size: Large
        - Sample ID: Barcode or num
        - Date Sampled: Extract the date written (leave blank if empty)
        - Machine: Equip ID
        - Component: Sample point
        - Serial Number: (leave blank)
        - Reference Lube: Ref Lube

        If Small:
        - Tube Size: Small
        - Sample ID: Barcode or num
        - Date Sampled: Extract the date written
        - Machine: RN
        - Component: Circled num + circled GS/NGS
        - Serial Number: SN
        - Reference Lube: (leave blank)

        Return ONLY a raw JSON object (no markdown formatting, no backticks) with exactly these keys:
        "Tube Size", "Sample ID", "Date Sampled", "Machine", "Component", "Serial Number", "Reference Lube"
        """

        max_retries = 3
        response = None

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model='gemini-3.7-flash',
                    contents=[prompt, *images]
                )
                break
            except Exception as api_error:
                if '503' in str(api_error) and attempt < max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
                elif attempt == max_retries - 1:
                    raise api_error

        response_text = response.text.strip().strip('```json').strip('```')
        extracted_data = json.loads(response_text)

        extracted_data["Filename"] = uploaded_file.name
        extracted_data["Date Received"] = date.today().strftime("%m/%d/%Y")
        
        # --- SOLANA INTEGRATION ---
        solana_explorer_url = anchor_to_solana(extracted_data)
        extracted_data["Solana Audit Trail"] = solana_explorer_url
        # --------------------------
        
        extracted_data["Status"] = "Success"
        return extracted_data

    except Exception as e:
        return {
            "Filename": uploaded_file.name,
            "Sample ID": "ERROR",
            "Status": f"Failed: {str(e)}"
        }


tab1, tab2, tab3 = st.tabs(["Image Upload", "Data Viewer", "Snowflake Analysis"])

with tab1:
    col_text, col_btn = st.columns([3, 1])
    with col_text:
        st.markdown("<h4 style='margin-top: 0.5rem;'>Upload Label Images (JPG, PNG) or Spin Videos (MP4, MOV)</h4>", unsafe_allow_html=True)
    with col_btn:
        if st.button("Clear All", use_container_width=True):
            st.session_state.uploader_key += 1
            st.session_state.generated_csv = None
            st.session_state.processed_df = None
            st.session_state.is_processing = False
            st.rerun()

    uploaded_files = st.file_uploader(
        "Upload Label Images or Videos",
        type=list(IMAGE_EXTS + VIDEO_EXTS),
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_key}",
        label_visibility="collapsed"
    )

    if uploaded_files:
        n_vid = sum(1 for f in uploaded_files if is_video_file(f))
        n_img = len(uploaded_files) - n_vid
        st.write(f"**{n_img} images, {n_vid} videos ready to process.**")

        user_file_name = st.text_input("Enter CSV file name:", "bulk_lab_samples_output")

        if st.button("Process All Labels", disabled=st.session_state.is_processing):
            st.session_state.is_processing = True
            st.rerun()

        if st.session_state.is_processing:
            try:
                client = genai.Client(api_key=api_key)
                progress_bar = st.progress(0)
                status_text = st.empty()
                results = []

                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = [executor.submit(process_label, client, f) for f in uploaded_files]
                    for i, future in enumerate(as_completed(futures)):
                        result = future.result()
                        results.append(result)
                        current_progress = (i + 1) / len(uploaded_files)
                        progress_bar.progress(current_progress)
                        status_text.text(f"Processed {i + 1} of {len(uploaded_files)} labels...")

                columns = [
                    "Sample ID", "Tube Size", "Date Sampled", "Date Received",
                    "Machine", "Component", "Serial Number", "Reference Lube",
                    "Solana Audit Trail"
                ]

                df = pd.DataFrame(results)
                for c in columns:
                    if c not in df.columns:
                        df[c] = None
                df = df.reindex(columns=columns)

                df['Date Sampled'] = pd.to_datetime(df['Date Sampled'], errors='coerce').dt.date

                # 1) Send CSV/data to Tiger Data
                db_uri = os.environ.get("TIGERDATA_URI")
                if db_uri:
                    try:
                        engine = create_engine(db_uri)
                        # Drop non-standard columns so it matches the SQL database schema
                        db_df = df.drop(columns=["Solana Audit Trail", "Tube Size", "TUBE_SIZE"], errors='ignore')
                        db_df.to_sql('lab_samples', con=engine, if_exists='append', index=False)
                        st.toast("Wrote batch to Tiger Data")
                    except Exception as e:
                        st.error(f"Failed to write to Tiger Data: {e}")
                else:
                    st.warning("TIGERDATA_URI is not set — skipped Tiger Data write.")

                # 2) Then send the same batch to Snowflake intake staging
                batch_id = None
                try:
                    batch_id = push_ocr_batch_to_snowflake(df)
                    st.session_state.last_snowflake_batch = batch_id
                    st.toast(f"Synced batch {batch_id} to Snowflake")
                except Exception as e:
                    st.error(f"Failed to write to Snowflake: {e}")

                # 3) Auto-run Grok QC after Tiger Data + Snowflake
                st.session_state.grok_qc_report = None
                st.session_state.grok_qc_error = None
                try:
                    with st.spinner("Grok automation: reviewing batch QC…"):
                        st.session_state.grok_qc_report = run_grok_batch_qc(df, batch_id)
                    st.toast("Grok QC complete")
                except Exception as e:
                    st.session_state.grok_qc_error = str(e)
                    st.warning(f"Grok automation skipped/failed: {e}")

                st.session_state.processed_df = df
                st.session_state.generated_csv = df.to_csv(index=False).encode('utf-8')
                st.session_state.csv_filename = f"{user_file_name}.csv"
            finally:
                # Reset the button state and refresh the UI when finished (or if an error occurs)
                st.session_state.is_processing = False
                st.rerun()

    # Tab 1: Popups removed; displays dataframe and download button directly
    if st.session_state.generated_csv is not None:
        st.dataframe(st.session_state.processed_df)

        st.download_button(
            label="Download Combined CSV",
            data=st.session_state.generated_csv,
            file_name=st.session_state.csv_filename,
            mime="text/csv",
        )
        st.markdown("#### Grok Automation — Batch QC")
        if st.session_state.grok_qc_report:
            st.success("Grok auto-reviewed this batch after Tiger Data + Snowflake sync.")
            st.markdown(st.session_state.grok_qc_report)
            st.caption(f"Logged to `{GROK_QC_LOG}` · model `{GROK_MODEL}`")
        elif st.session_state.grok_qc_error:
            st.error(f"Grok QC error: {st.session_state.grok_qc_error}")

with tab2:
    st.subheader("Live Database Records")

    col_btn, col_status = st.columns([1, 1])
    with col_btn:
        st.button("Refresh Table", use_container_width=False)
    with col_status:
        st.markdown("""
            <div class="alert-container">
                <input type="checkbox" id="close-alert">
                <div class="alert-box">
                    Successfully connected!
                    <label for="close-alert" class="close-btn">✖</label>
                </div>
            </div>
        """, unsafe_allow_html=True)

    try:
        db_uri = os.environ.get("TIGERDATA_URI")
        if db_uri:
            engine = create_engine(db_uri)
            db_df = pd.read_sql_table('lab_samples', con=engine)

            filtered_df = db_df.copy()
            
            # Action Row: Filter Options, Clear Button, and Right-Aligned Export Popover
            col_filter, col_clear, col_gap, col_export = st.columns([1.8, 1.2, 5.0, 2.0])
            with col_filter:
                with st.popover("⚙️ Filter Data Options"):
                    for col in db_df.columns:
                        col_norm = col.strip().lower().replace(" ", "_")
                        
                        # Date selector for Date Sampled and Date Received
                        if col_norm in ["date_sampled", "date_received"]:
                            date_val = st.date_input(
                                f"Filter by {col}",
                                value=(),
                                key=f"filter_date_{col}",
                                help="Select one date for exact match, or click two dates to select a range"
                            )
                            if date_val:
                                col_dates = safe_parse_dates(filtered_df[col])
                                if isinstance(date_val, (tuple, list)):
                                    if len(date_val) == 1:
                                        d_single = date_val[0]
                                        if d_single:
                                            filtered_df = filtered_df[col_dates == d_single]
                                    elif len(date_val) >= 2:
                                        d_start, d_end = date_val[0], date_val[1]
                                        if d_start and d_end:
                                            if d_start > d_end:
                                                d_start, d_end = d_end, d_start
                                            filtered_df = filtered_df[(col_dates >= d_start) & (col_dates <= d_end)]
                                elif isinstance(date_val, (date, datetime)):
                                    d_single = date_val if isinstance(date_val, date) else date_val.date()
                                    filtered_df = filtered_df[col_dates == d_single]
                        else:
                            # Standard categorical multiselect or text search
                            unique_vals = db_df[col].dropna().unique().tolist()
                            if len(unique_vals) <= 40:
                                selected = st.multiselect(f"Filter by {col}", options=unique_vals, key=f"filter_{col}")
                                if selected:
                                    filtered_df = filtered_df[filtered_df[col].isin(selected)]
                            else:
                                text_search = st.text_input(f"Search {col}", key=f"filter_{col}")
                                if text_search:
                                    filtered_df = filtered_df[filtered_df[col].astype(str).str.contains(text_search, case=False, na=False)]
            
            with col_clear:
                if st.button("Clear Filters"):
                    for key in list(st.session_state.keys()):
                        if key.startswith("filter_"):
                            del st.session_state[key]
                    st.rerun()

            with col_export:
                with st.popover("📥 Export Data", use_container_width=True):
                    st.markdown(f"**Export Visible Samples**<br><small>{len(filtered_df)} record(s) matching current filters.</small>", unsafe_allow_html=True)
                    
                    # CSV Export
                    csv_bytes = filtered_df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📄 Export as CSV",
                        data=csv_bytes,
                        file_name="visible_lab_samples.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                    
                    # JSON Export
                    json_bytes = filtered_df.to_json(orient='records', indent=2).encode('utf-8')
                    st.download_button(
                        label="📋 Export as JSON",
                        data=json_bytes,
                        file_name="visible_lab_samples.json",
                        mime="application/json",
                        use_container_width=True
                    )
                    
                    # Excel Export
                    excel_bytes = df_to_excel_bytes(filtered_df)
                    st.download_button(
                        label="📊 Export as Excel (.xlsx)",
                        data=excel_bytes,
                        file_name="visible_lab_samples.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                    
                    # PDF Export
                    pdf_bytes = df_to_pdf_bytes(filtered_df, title="MRG Labs - Visible Samples")
                    st.download_button(
                        label="📑 Export as PDF",
                        data=pdf_bytes,
                        file_name="visible_lab_samples.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )

            st.caption(f"Showing **{len(filtered_df)}** of **{len(db_df)}** total records from database.")
            st.dataframe(filtered_df, use_container_width=True)

        else:
            st.warning("TIGERDATA_URI environment variable is not set.")
    except Exception as e:
        st.error(f"Could not connect to database: {e}")

# ---------- Snowflake Sample Intake (Dashboard + Reasoning AI) ----------
with tab3:

    # CSV File Upload Field (Analysis is performed on this uploaded CSV)
    col_csv, col_tab1_ref = st.columns([3, 1])
    with col_csv:
        uploaded_csv_file = st.file_uploader(
            "Upload Lab Samples CSV for Analysis",
            type=["csv"],
            key="sf_csv_upload",
            help="Upload the weekly OCR output or intake CSV to run deep analysis"
        )
    with col_tab1_ref:
        if st.session_state.processed_df is not None:
            st.write("")
            st.write("")
            if st.button("📂 Load Tab 1 OCR Batch", use_container_width=True):
                st.session_state.use_tab1_in_tab3 = True
                st.rerun()

    raw_data = None
    data_source_name = ""

    if uploaded_csv_file is not None:
        try:
            raw_data = pd.read_csv(uploaded_csv_file, dtype=str)
            data_source_name = f"Uploaded CSV: {uploaded_csv_file.name}"
        except Exception as e:
            st.error(f"Failed to read uploaded CSV file: {e}")
    elif st.session_state.get('use_tab1_in_tab3', False) and st.session_state.processed_df is not None:
        raw_data = st.session_state.processed_df.copy()
        data_source_name = f"Recent Tab 1 OCR Batch ({len(raw_data)} samples)"
    elif st.session_state.processed_df is not None:
        raw_data = st.session_state.processed_df.copy()
        data_source_name = f"Tab 1 Batch ({len(raw_data)} samples)"

    if raw_data is None or raw_data.empty:
        st.info("Please upload a lab samples CSV above (or process labels in Tab 1) to inspect grease intake trends and run AI data intelligence.")
    else:
        # Validate data with strict differentiation between Small and Large tube sizes
        raw_sig = tab3_signature(raw_data)
        edit_state = get_tab3_state(raw_sig)
        raw_base = normalize_raw(raw_data)
        data = validate_dataframe(apply_edit_overlay(raw_base, edit_state["edits"]))
        data, cell_flags = apply_flag_overrides(data, edit_state["flags"])

        # 3 Sub-tabs (Flagged samples sub-tab removed)
        tab_dash, tab_chat, tab_data = st.tabs(
            ["Dashboard", "Analyst", "Data"]
        )

        with tab_dash:
            # Minimalist Bento Box SaaS Layout
            total_n = len(data)
            flagged_n = int(data["IS_FLAGGED"].sum())
            clean_n = total_n - flagged_n
            quality_score = round((clean_n / total_n * 100), 1) if total_n > 0 else 0
            
            small_n = int((data["TUBE_SIZE"] == "Small").sum())
            large_n = int((data["TUBE_SIZE"] == "Large").sum())
            small_pct = round(small_n / total_n * 100, 1) if total_n > 0 else 0
            large_pct = round(large_n / total_n * 100, 1) if total_n > 0 else 0
            
            t_days = data["TURNAROUND_DAYS"].dropna()
            avg_turnaround = round(float(t_days.mean()), 1) if len(t_days) > 0 else 0
            
            mach_series = data["MACHINE"].dropna()
            mach_series = mach_series[~mach_series.isin(["", "None", "nan", "NaT", "<NA>"])]
            unique_machines = mach_series.nunique()
            
            comp_series = data["COMPONENT"].dropna()
            comp_series = comp_series[~comp_series.isin(["", "None", "nan", "NaT", "<NA>"])]
            unique_components = comp_series.nunique()

            # Bento KPI Cards Row
            k1, k2, k3, k4, k5 = st.columns(5)
            with k1:
                st.markdown(f"""
                    <div class="bento-card">
                        <div class="bento-header">
                            <span class="bento-title">Total Samples</span>
                            <span class="bento-badge bento-badge-info">Intake</span>
                        </div>
                        <div class="bento-metric">{total_n}</div>
                        <div class="bento-subtitle">{data_source_name}</div>
                    </div>
                """, unsafe_allow_html=True)
            with k2:
                q_badge = "bento-badge-success" if quality_score >= 90 else "bento-badge-warning"
                st.markdown(f"""
                    <div class="bento-card">
                        <div class="bento-header">
                            <span class="bento-title">Quality & Compliance</span>
                            <span class="bento-badge {q_badge}">{quality_score}% Clean</span>
                        </div>
                        <div class="bento-metric">{flagged_n} <span style="font-size: 14px; font-weight: 500; color: #64748b;">flagged</span></div>
                        <div class="bento-subtitle">{clean_n} verified records</div>
                    </div>
                """, unsafe_allow_html=True)
            with k3:
                st.markdown(f"""
                    <div class="bento-card">
                        <div class="bento-header">
                            <span class="bento-title">Tube Size Split</span>
                            <span class="bento-badge bento-badge-info">Grease Thief</span>
                        </div>
                        <div class="bento-metric">{large_n}L / {small_n}S</div>
                        <div class="bento-subtitle">{large_pct}% Large • {small_pct}% Small</div>
                    </div>
                """, unsafe_allow_html=True)
            with k4:
                t_badge = "bento-badge-success" if avg_turnaround <= 7 else "bento-badge-warning"
                st.markdown(f"""
                    <div class="bento-card">
                        <div class="bento-header">
                            <span class="bento-title">Avg Turnaround</span>
                            <span class="bento-badge {t_badge}">{avg_turnaround} d</span>
                        </div>
                        <div class="bento-metric">{avg_turnaround} <span style="font-size: 14px; font-weight: 500; color: #64748b;">days</span></div>
                        <div class="bento-subtitle">Field draw to lab intake</div>
                    </div>
                """, unsafe_allow_html=True)
            with k5:
                st.markdown(f"""
                    <div class="bento-card">
                        <div class="bento-header">
                            <span class="bento-title">Monitored Assets</span>
                            <span class="bento-badge bento-badge-info">Assets</span>
                        </div>
                        <div class="bento-metric">{unique_machines}</div>
                        <div class="bento-subtitle">{unique_components} distinct sample points</div>
                    </div>
                """, unsafe_allow_html=True)

            st.write("")

            # Bento Grid Visualizations (Row 1)
            r1_c1, r1_c2 = st.columns(2)
            with r1_c1:
                with st.container(border=True):
                    st.markdown("##### 📈 Sample Intake & Collection Velocity")
                    st.caption("Distribution of grease samples across received and sampled dates")
                    date_col = "DATE_RECEIVED" if "DATE_RECEIVED" in data.columns else "DATE_SAMPLED"
                    date_counts = data[date_col].dropna().value_counts().sort_index()
                    if not date_counts.empty:
                        st.bar_chart(date_counts.rename("Samples"), color="#004B87")
                    else:
                        st.info("No date records available to plot.")

            with r1_c2:
                with st.container(border=True):
                    st.markdown("##### ⚙️ High-Frequency Assets (Repeat Sampling Trend)")
                    st.caption("Top machines by sample count — assets with frequent grease draws")
                    if not mach_series.empty:
                        top_mach = mach_series.value_counts().head(8)
                        st.bar_chart(top_mach.rename("Samples"), color="#005A9E")
                    else:
                        st.info("No machine records available to plot.")

            # Bento Grid Visualizations (Row 2)
            r2_c1, r2_c2 = st.columns(2)
            with r2_c1:
                with st.container(border=True):
                    st.markdown("##### 📍 Component & Sample Point Distribution")
                    st.caption("Breakdown across bearings, gearboxes, and GS/NGS points")
                    if not comp_series.empty:
                        top_comp = comp_series.value_counts().head(8)
                        st.bar_chart(top_comp.rename("Samples"), color="#0284C7")
                    else:
                        st.info("No component records available to plot.")

            with r2_c2:
                with st.container(border=True):
                    st.markdown("##### 🧴 Reference Lubricants & Consolidation")
                    st.caption("Primary grease formulations registered across machinery")
                    lube_series = data["REFERENCE_LUBE"].dropna()
                    lube_series = lube_series[~lube_series.isin(["", "None", "nan", "NaT", "<NA>"])]
                    if not lube_series.empty:
                        top_lubes = lube_series.value_counts().head(8)
                        st.bar_chart(top_lubes.rename("Samples"), color="#4F46E5")
                    else:
                        st.caption("Reference Lubes are recorded on Large sample tubes (omitted on Small RN tubes).")
                        st.info("No reference lubes recorded in this batch.")

            # Bento Grid Visualizations (Row 3: Transit Latency & Quality Integrity)
            r3_c1, r3_c2 = st.columns(2)
            with r3_c1:
                with st.container(border=True):
                    st.markdown("##### ⏱️ Transit Latency & Turnaround Distribution")
                    st.caption("Delay from sampling to lab receipt (identifies shelf-life / transit lag)")
                    def bucket_turnaround(d):
                        if pd.isna(d) or d is None:
                            return "Unknown / Bad Date"
                        d_val = int(d)
                        if d_val <= 3:
                            return "0-3 Days (Optimal)"
                        elif d_val <= 7:
                            return "4-7 Days (Standard)"
                        elif d_val <= 14:
                            return "8-14 Days (Delayed)"
                        else:
                            return "15+ Days (High Latency)"
                    b_counts = data["TURNAROUND_DAYS"].apply(bucket_turnaround).value_counts()
                    st.bar_chart(b_counts.rename("Samples"), color="#E11D48")

            with r3_c2:
                with st.container(border=True):
                    st.markdown("##### 🛡️ Data Quality & Audit Categorization")
                    st.caption("Audit classification of sample anomalies in this batch")
                    audit_cats = {
                        "Clean Records": clean_n,
                        "Duplicate IDs": int(data["IS_DUPLICATE"].sum()),
                        "Missing Universal": int((data["HAS_MISSING_FIELD"] & data["MISSING_REASONS"].str.contains("Sample ID|Machine|Component", na=False)).sum()),
                        "Missing Small-Specific": int((data["HAS_MISSING_FIELD"] & (data["TUBE_SIZE"] == "Small") & data["MISSING_REASONS"].str.contains("Date Sampled|Serial Number", na=False)).sum()),
                        "Missing Large-Specific": int((data["HAS_MISSING_FIELD"] & (data["TUBE_SIZE"] == "Large") & data["MISSING_REASONS"].str.contains("Reference Lube", na=False)).sum()),
                        "Bad / Inverted Dates": int(data["HAS_BAD_DATE"].sum())
                    }
                    st.bar_chart(pd.Series(audit_cats).rename("Samples"), color="#F59E0B")

            col_run_ai, col_clear_ai = st.columns([2, 8])
            with col_run_ai:
                if st.button("⚡ Run AI Pattern Analysis", key="btn_run_deep_ai"):
                    client = genai.Client(api_key=api_key)
                    with st.spinner("Analyzing grease sample patterns with high-level reasoning model..."):
                        try:
                            analysis_result = generate_ai_grease_analysis(client, data)
                            st.session_state.ai_grease_analysis = analysis_result
                        except Exception as ai_err:
                            st.error(f"Reasoning model error: {ai_err}")

            if st.session_state.ai_grease_analysis:
                with st.container(border=True):
                    st.markdown(st.session_state.ai_grease_analysis)

        with tab_chat:
            st.markdown("##### 💬 Ask the Data — High-Level Reasoning Intelligence")
            st.caption(
                "Ask complex questions regarding repeat machine sampling, grease degradation risks, "
                "lubrication consistency, turnaround latency, and label anomalies."
            )

            # Quick Prompt Chips
            c_q1, c_q2, c_q3 = st.columns(3)
            quick_prompt = None
            with c_q1:
                if st.button("🔍 Problem Equipment Clusters", use_container_width=True, key="chip_prob_mach"):
                    quick_prompt = "Top 5 machines by sample count. Likely wear problem? One line each."
            with c_q2:
                if st.button("⚠️ Flagged Sample Audit", use_container_width=True, key="chip_flagged_audit"):
                    quick_prompt = "List flagged samples as: ID, issue, fix. Group by Small and Large tubes."
            with c_q3:
                if st.button("⏱️ Turnaround Latency Analysis", use_container_width=True, key="chip_latency"):
                    quick_prompt = "Top 5 longest turnarounds: ID, machine, days. Integrity risk?"

            # Render message history
            for m in st.session_state.msgs:
                role = m.get("role", "user")
                content = m.get("content", "")
                with st.chat_message(role):
                    st.markdown(content)

            chat_input_val = st.chat_input("Ask Cortex/Gemini about this intake data…")
            prompt_to_send = quick_prompt or chat_input_val

            if prompt_to_send:
                st.session_state.msgs.append({"role": "user", "content": prompt_to_send})
                with st.chat_message("user"):
                    st.markdown(prompt_to_send)

                with st.chat_message("assistant"):
                    with st.spinner("Analyzing with high-level reasoning model..."):
                        try:
                            client = genai.Client(api_key=api_key)
                            response_text = ask_reasoning_chat(client, st.session_state.msgs, data)
                            st.markdown(response_text)
                            st.session_state.msgs.append({"role": "assistant", "content": response_text})
                        except Exception as e:
                            st.error(f"Error querying reasoning model: {e}")

        with tab_data:
            _saved = st.session_state.pop("tab3_msg", None)
            if _saved:
                getattr(st, _saved[0])(_saved[1])
            col_flag_toggle, col_flag_info = st.columns([2, 3])
            with col_flag_toggle:
                # Under data, show only flagged samples is checked by default
                show_only_flagged = st.checkbox("Show only flagged samples", value=True, key="chk_flagged_default")

            df_to_show = data.copy()
            if show_only_flagged:
                df_to_show = df_to_show[df_to_show["IS_FLAGGED"] == True]

            with col_flag_info:
                flagged_count = int(data["IS_FLAGGED"].sum())
                st.caption(f"Showing **{len(df_to_show)}** of **{len(data)}** samples (**{flagged_count}** flagged for review).")

            if df_to_show.empty:
                if show_only_flagged:
                    st.success("🎉 No flagged samples found! All samples meet MRG Labs quality criteria.")
                else:
                    st.info("No records in this dataset.")
            else:
                view_cols = [c for c in GRID_COLS if c in df_to_show.columns]
                grid_rows = []
                for idx, r in df_to_show.iterrows():
                    row_d = {"_i": int(idx)}
                    for c in view_cols:
                        v = r.get(c)
                        row_d[c] = "" if _is_blank(v) else (str(int(v)) if c == "TURNAROUND_DAYS" else str(v))
                    grid_rows.append(row_d)
                shown = {int(i) for i in df_to_show.index}
                grid_flags = {}
                for (ri, col), items in cell_flags.items():
                    if ri in shown and col in view_cols:
                        tip = "; ".join(("Missing " + t) if k == "missing" else t for k, t in items)
                        grid_flags.setdefault(str(ri), {})[col] = tip

                grid_version = f"{raw_sig}-{edit_state['version']}"
                grid_value = grid_editor(
                    rows=grid_rows,
                    columns=view_cols,
                    editable=[c for c in DB_EDIT_COLS if c in view_cols],
                    flags=grid_flags,
                    version=grid_version,
                    key="tab3_grid",
                    default=None,
                )

                if (
                    isinstance(grid_value, dict)
                    and grid_value.get("action") == "save"
                    and grid_value.get("version") == grid_version
                    and grid_value.get("token") != edit_state.get("handled")
                ):
                    edit_state["handled"] = grid_value["token"]
                    level, message = save_grid_changes(grid_value, raw_base, edit_state)
                    if level == "error":
                        st.error(message)
                    else:
                        edit_state["version"] += 1
                        st.session_state["tab3_msg"] = (level, message)
                        st.rerun()