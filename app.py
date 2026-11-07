import streamlit as st
from google import genai
import pandas as pd
from datetime import date
from PIL import Image
import json
import time

# Configure the Streamlit page
st.set_page_config(page_title="MRG Labs Label Scanner", layout="centered")

st.title("MRG Labs: Sample Label Scanner")
st.write("Upload an image of a Large or Small sample label to generate a formatted CSV.")

# Input for Gemini API Key
api_key = st.text_input("Enter your Google Gemini API Key:", type="password")

# File uploader
uploaded_file = st.file_uploader("Upload Label Image (JPG, PNG)", type=["jpg", "jpeg", "png"])

if uploaded_file is not None and api_key:
    # Display the uploaded image
    image = Image.open(uploaded_file)
    st.image(image, caption="Uploaded Label")

    if st.button("Process Label"):
        with st.spinner("Analyzing label with Gemini..."):
            try:
                client = genai.Client(api_key=api_key)
                
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
                
                # --- NEW RETRY LOGIC ---
                max_retries = 3
                response = None
                
                for attempt in range(max_retries):
                    try:
                        response = client.models.generate_content(
                            model='gemini-3.8-flash',
                            contents=[prompt, image]
                        )
                        break  # Break out of the loop if successful
                    except Exception as api_error:
                        if '503' in str(api_error) and attempt < max_retries - 1:
                            # Wait for 2 seconds, then 4 seconds before trying again
                            time.sleep(2 ** (attempt + 1))
                        else:
                            raise api_error  # Raise if it's not a 503 or we're out of retries
                # -----------------------

                # Parse the JSON response
                response_text = response.text.strip().strip('```json').strip('```')
                extracted_data = json.loads(response_text)
                
                # Add "Date Received" as today's date
                extracted_data["Date Received"] = date.today().strftime("%m/%d/%Y")
                
                columns = [
                    "Sample ID", 
                    "Date Sampled", 
                    "Date Received", 
                    "Machine", 
                    "Component", 
                    "Serial Number", 
                    "Reference Lube"
                ]
                
                df = pd.DataFrame([extracted_data], columns=columns)
                
                st.success("Label processed successfully!")
                st.dataframe(df)
                
                csv = df.to_csv(index=False).encode('utf-8')
                
                st.download_button(
                    label="Download CSV",
                    data=csv,
                    file_name="lab_sample_output.csv",
                    mime="text/csv",
                )
                
            except Exception as e:
                st.error(f"An error occurred: {e}")
elif not api_key:
    st.info("Please enter your Gemini API key to proceed.")
