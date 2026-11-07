import streamlit as st
import streamlit.components.v1 as components
from google import genai
import pandas as pd
from datetime import date
from PIL import Image
import json
import time
import os
import base64
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import create_engine
import snowflake.snowpark as snowpark
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

# Configure the Streamlit page
st.set_page_config(page_title="MRG Labs Label Scanner", layout="centered")

# Inject MRG Labs CSS Theme and Custom Behaviors
st.markdown("""
    <style>
        /* MRG Labs Brand Styling */
        :root {
            --mrg-blue: #004B87;
            --mrg-light-blue: #005A9E;
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
    </style>
""", unsafe_allow_html=True)

st.title("MRG Labs: Sample Label Scanner")

# Initialize session state variables
if 'uploader_key' not in st.session_state:
    st.session_state.uploader_key = 0
if 'generated_csv' not in st.session_state:
    st.session_state.generated_csv = None
if 'csv_filename' not in st.session_state:
    st.session_state.csv_filename = None
if 'processed_df' not in st.session_state:
    st.session_state.processed_df = None

api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    st.error("Server Configuration Error: GEMINI_API_KEY environment variable is not set.")
    st.stop()

def get_image_base64(image):
    """Helper function to convert PIL Image to base64 for custom HTML rendering."""
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def process_label(client, uploaded_file):
    try:
        image = Image.open(uploaded_file)
        image.thumbnail((1024, 1024))
        
        prompt = """
        You are an OCR and data extraction assistant for a laboratory. 
        Analyze the provided image of a sample label. Determine if it is a 'Large' or 'Small' sample label based on the fields present.
        
        Extract the data to match these exact rules:
        If Large:
        - Sample ID: Barcode or num
        - Date Sampled: Extract the date written (leave blank if empty)
        - Machine: Equip ID
        - Component: Sample point
        - Serial Number: (leave blank)
        - Reference Lube: Ref Lube
        
        If Small:
        - Sample ID: Barcode or num
        - Date Sampled: Extract the date written
        - Machine: RN
        - Component: Circled num + circled GS/NGS
        - Serial Number: SN
        - Reference Lube: (leave blank)
        
        Return ONLY a raw JSON object (no markdown formatting, no backticks) with exactly these keys:
        "Sample ID", "Date Sampled", "Machine", "Component", "Serial Number", "Reference Lube"
        """
        
        max_retries = 3
        response = None
        
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model='gemini-3.8-flash',
                    contents=[prompt, image]
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
        extracted_data["Status"] = "Success"
        
        return extracted_data
        
    except Exception as e:
        return {
            "Filename": uploaded_file.name,
            "Sample ID": "ERROR",
            "Status": f"Failed: {str(e)}"
        }

tab1, tab2 = st.tabs(["Image Upload", "Data Viewer"])

with tab1:
    col_text, col_btn = st.columns([3, 1])
    with col_text:
        st.markdown("<h4 style='margin-top: 0.5rem;'>Upload Label Images (JPG, PNG)</h4>", unsafe_allow_html=True)
    with col_btn:
        if st.button("Clear All", use_container_width=True):
            st.session_state.uploader_key += 1
            st.session_state.generated_csv = None
            st.session_state.processed_df = None
            st.rerun()

    uploaded_files = st.file_uploader(
        "Upload Label Images (JPG, PNG)", 
        type=["jpg", "jpeg", "png"], 
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_key}",
        label_visibility="collapsed"
    )

    if uploaded_files:
        st.write(f"**{len(uploaded_files)} images ready to process.**")
        
        user_file_name = st.text_input("Enter CSV file name:", "bulk_lab_samples_output")
        
        if st.button("Process All Labels"):
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
                "Sample ID", "Date Sampled", "Date Received", 
                "Machine", "Component", "Serial Number", "Reference Lube",
            ]
            
            df = pd.DataFrame(results)
            df = df.reindex(columns=columns)

            df['Date Sampled'] = pd.to_datetime(df['Date Sampled'], errors='coerce').dt.date

            db_uri = os.environ.get("TIGERDATA_URI")
            if db_uri:
                try:
                    engine = create_engine(db_uri)
                    df.to_sql('lab_samples', con=engine, if_exists='append', index=False)
                except Exception as e:
                    st.error(f"Failed to write to database: {e}")
            
            st.session_state.processed_df = df
            st.session_state.generated_csv = df.to_csv(index=False).encode('utf-8')
            st.session_state.csv_filename = f"{user_file_name}.csv"
            
            st.session_state.uploader_key += 1
            st.rerun()

    if st.session_state.generated_csv is not None:
        st.success("All labels processed successfully! The upload area has been cleared for your next batch.")
        st.dataframe(st.session_state.processed_df)
        
        st.download_button(
            label="Download Combined CSV",
            data=st.session_state.generated_csv,
            file_name=st.session_state.csv_filename,
            mime="text/csv",
        )

    # --- MINESWEEPER COMPONENT ---
    st.markdown("---")
    st.markdown("<h5 style='text-align: center; color: #004B87;'>Waiting for images to process? Play a round!</h5>", unsafe_allow_html=True)
    
    minesweeper_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <style>
        body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; margin: 0; padding-top: 10px; color: #333; }
        #board { display: grid; grid-template-columns: repeat(10, 30px); gap: 2px; margin-top: 10px; }
        .cell { width: 30px; height: 30px; background-color: #f0f2f6; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 14px; cursor: pointer; border-radius: 4px; user-select: none; }
        .cell:hover { background-color: #e0e2e6; }
        .cell.revealed { background-color: #ffffff; border: 1px solid #ddd; cursor: default; box-sizing: border-box; }
        .cell.mine { background-color: #ffcccc; }
        #status { margin-top: 15px; font-weight: bold; min-height: 20px; color: #004B87; }
        button { margin-top: 10px; padding: 8px 16px; border: none; background: #004B87; color: white; border-radius: 4px; cursor: pointer; font-size: 14px; transition: all 0.3s ease; }
        button:hover { background: #005A9E; }
    </style>
    </head>
    <body>
        <div id="board"></div>
        <div id="status"></div>
        <button onclick="initGame()">Restart Game</button>
        
        <script>
            const size = 10;
            const mineCount = 12;
            let board = [];
            let revealed = 0;
            let gameOver = false;

            function initGame() {
                board = Array(size).fill().map(() => Array(size).fill({ mine: false, revealed: false, flag: false, count: 0 }));
                gameOver = false;
                revealed = 0;
                document.getElementById('status').innerText = "Right-click to flag mines";
                
                let minesPlaced = 0;
                while (minesPlaced < mineCount) {
                    let r = Math.floor(Math.random() * size);
                    let c = Math.floor(Math.random() * size);
                    if (!board[r][c].mine) {
                        board[r][c] = { ...board[r][c], mine: true };
                        minesPlaced++;
                    }
                }

                for (let r = 0; r < size; r++) {
                    for (let c = 0; c < size; c++) {
                        if (!board[r][c].mine) {
                            let count = 0;
                            for (let dr = -1; dr <= 1; dr++) {
                                for (let dc = -1; dc <= 1; dc++) {
                                    if (r+dr >= 0 && r+dr < size && c+dc >= 0 && c+dc < size && board[r+dr][c+dc].mine) count++;
                                }
                            }
                            board[r][c] = { ...board[r][c], count };
                        }
                    }
                }
                render();
            }

            function render() {
                const b = document.getElementById('board');
                b.innerHTML = '';
                for (let r = 0; r < size; r++) {
                    for (let c = 0; c < size; c++) {
                        let cell = document.createElement('div');
                        cell.className = 'cell';
                        if (board[r][c].revealed) {
                            cell.classList.add('revealed');
                            if (board[r][c].mine) {
                                cell.classList.add('mine');
                                cell.innerText = '💣';
                            } else if (board[r][c].count > 0) {
                                cell.innerText = board[r][c].count;
                                // Color code numbers
                                const colors = ['#0000FF', '#008000', '#FF0000', '#000080', '#800000', '#008080', '#000000', '#808080'];
                                cell.style.color = colors[board[r][c].count - 1];
                            }
                        } else if (board[r][c].flag) {
                            cell.innerText = '🚩';
                        }
                        
                        cell.onclick = () => reveal(r, c);
                        cell.oncontextmenu = (e) => { e.preventDefault(); toggleFlag(r, c); };
                        b.appendChild(cell);
                    }
                }
            }

            function reveal(r, c) {
                if (gameOver || board[r][c].revealed || board[r][c].flag) return;
                
                let cell = board[r][c];
                cell.revealed = true;
                
                if (cell.mine) {
                    gameOver = true;
                    document.getElementById('status').innerText = "Boom! Game Over.";
                    document.getElementById('status').style.color = "red";
                    revealAll();
                } else {
                    revealed++;
                    if (revealed === size * size - mineCount) {
                        gameOver = true;
                        document.getElementById('status').innerText = "You Win! 🏆";
                        document.getElementById('status').style.color = "green";
                    } else if (cell.count === 0) {
                        for (let dr = -1; dr <= 1; dr++) {
                            for (let dc = -1; dc <= 1; dc++) {
                                if (r+dr >= 0 && r+dr < size && c+dc >= 0 && c+dc < size) reveal(r+dr, c+dc);
                            }
                        }
                    }
                }
                render();
            }

            function toggleFlag(r, c) {
                if (gameOver || board[r][c].revealed) return;
                board[r][c].flag = !board[r][c].flag;
                render();
            }

            function revealAll() {
                for (let r = 0; r < size; r++) {
                    for (let c = 0; c < size; c++) {
                        board[r][c].revealed = true;
                    }
                }
            }

            initGame();
        </script>
    </body>
    </html>
    """
    components.html(minesweeper_html, height=450)

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
            
            # Filtering Options
            st.markdown("##### Filter Data")
            filter_col1, filter_col2 = st.columns(2)
            with filter_col1:
                machine_filter = st.text_input("Filter by Machine (leave blank for all)")
            with filter_col2:
                available_components = db_df['Component'].dropna().unique().tolist()
                comp_filter = st.multiselect("Filter by Component", options=available_components)
            
            # Apply Filters
            filtered_df = db_df.copy()
            if machine_filter:
                filtered_df = filtered_df[filtered_df['Machine'].astype(str).str.contains(machine_filter, case=False, na=False)]
            if comp_filter:
                filtered_df = filtered_df[filtered_df['Component'].isin(comp_filter)]
            
            # Note: Streamlit's native dataframe supports column sorting by clicking the headers
            st.dataframe(filtered_df.tail(100), use_container_width=True) 

            st.divider()
            st.markdown("#### ❄️ Snowflake AI Data Analyst")

            if st.button("Ask Snowflake for Insights", use_container_width=True):
                if filtered_df.empty:
                    st.warning("No data available to analyze.")
                else:
                    with st.spinner("Connecting to Snowflake Cortex..."):
                        try:
                            # Load the private key
                            with open("rsa_key.p8", "rb") as key:
                                p_key = serialization.load_pem_private_key(
                                    key.read(),
                                    password=None,
                                    backend=default_backend()
                                )
                            
                            pkb = p_key.private_bytes(
                                encoding=serialization.Encoding.DER,
                                format=serialization.PrivateFormat.PKCS8,
                                encryption_algorithm=serialization.NoEncryption()
                            )

                            # 1. Authenticate with Snowflake via Key Pair (Bypasses MFA)
                            conn_params = {
                                "account": os.environ.get("SNOWFLAKE_ACCOUNT"),
                                "user": os.environ.get("SNOWFLAKE_USER"),
                                "private_key": pkb,
                                "role": "ACCOUNTADMIN",
                                "warehouse": "COMPUTE_WH",
                                "database": "HACKATHON_DB",
                                "schema": "PUBLIC"
                            }
                            session = snowpark.Session.builder.configs(conn_params).create()
                            
                            # 2. Push Tiger DB data to Snowflake
                            session.write_pandas(
                                filtered_df, 
                                table_name="LAB_SAMPLES_TEMP", 
                                auto_create_table=True, 
                                overwrite=True
                            )
                            
                            # 3. Generate Insights using Snowflake Cortex (Native AI)
                            summary_stats = filtered_df.describe(include='all').to_json()
                            prompt = (
                                f"You are a laboratory data analyst. Based on this summary of recent equipment "
                                f"fluid samples, provide 3 brief, actionable insights or identify anomalies: {summary_stats}"
                            )
                            
                            # Escape single quotes to prevent SQL syntax errors
                            safe_prompt = prompt.replace("'", "''")
                            
                            # Query Cortex for Llama 3 LLM insights
                            sql_query = f"SELECT AI_COMPLETE('llama3.1-8b', '{safe_prompt}') as INSIGHT"
                            cortex_result = session.sql(sql_query).collect()
                            
                            st.success("Analysis Complete!")
                            st.write(cortex_result[0]['INSIGHT'])
                            
                        except Exception as e:
                            st.error(f"Snowflake Integration Error: {e}")



        else:
            st.warning("TIGERDATA_URI environment variable is not set.")
    except Exception as e:
        st.error(f"Could not connect to database: {e}")