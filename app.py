from flask import Flask, request, jsonify
from flask_cors import CORS
from azure.storage.blob import BlobServiceClient, ContainerClient
import os
import requests
import openai
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.models import VectorizedQuery
import pyodbc
from azure.monitor.opentelemetry import configure_azure_monitor
import base64
from openai import OpenAI

app = Flask(__name__)
CORS(app,origins=["http://20.100.58.26"])

# Use environment variable in production
AZURE_CONNECTION_STRING = "BlobEndpoint=https://picturesupload.blob.core.windows.net/;QueueEndpoint=https://picturesupload.queue.core.windows.net/;FileEndpoint=https://picturesupload.file.core.windows.net/;TableEndpoint=https://picturesupload.table.core.windows.net/;SharedAccessSignature=sv=2025-11-05&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2026-12-30T19:18:52Z&st=2026-04-14T11:03:52Z&spr=https&sig=nmCRxXbHrrnUPqnZ0TP%2BKS%2FT5FTKgFNBEeBL1xHvWV4%3D"
CONTAINER_NAME = "images"
openai.api_key = os.getenv("OPENAI_API_KEY")
SEARCH_ENDPOINT = "https://cw2aivision.search.windows.net"
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY")
SEARCH_INDEX = "image-index"

client = OpenAI(openai.api_key)

configure_azure_monitor()


search_client = SearchClient(
    endpoint=SEARCH_ENDPOINT,
    index_name=SEARCH_INDEX,
    credential=AzureKeyCredential(SEARCH_KEY)
)

blob_service_client = BlobServiceClient.from_connection_string(
    AZURE_CONNECTION_STRING
)

# Connection details to Azure SQL DB
server = 'tcp:photos-db-server.database.windows.net,1433'
database = 'photos-db'
username = os.getenv("DB_USERNAME")
password = os.getenv("DB_PASSWORD")
driver = '{ODBC Driver 18 for SQL Server}'

# Connection string
conn_str = f"""
DRIVER={driver};
SERVER={server};
DATABASE={database};
UID={username};
PWD={password};
Encrypt=yes;
TrustServerCertificate=no;
Connection Timeout=30;
"""


def generate_text_embedding(query_text, model="text-embedding-3-large"):
    if not openai.api_key:
        raise ValueError("Please set OPENAI_API_KEY environment variable")
    
    response = openai.embeddings.create(
        input=query_text,
        model=model
    )
    
    embedding_vector = response.data[0].embedding
    
    return embedding_vector

@app.route("/upload", methods=["POST"])
def upload_image():
    if "image" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["image"]

    blob_client = blob_service_client.get_blob_client(
        container=CONTAINER_NAME,
        blob=file.filename
    )

    blob_client.upload_blob(file, overwrite=True)

    #Generate AI Caption

    image_url = blob_client.url

    # Download image locally
    img_response = requests.get(image_url)

    if img_response.status_code != 200:
        print("Failed to download image")
        exit()

    # Convert image to base64
    base64_image = base64.b64encode(img_response.content).decode("utf-8")

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Generate a short caption for the images with just five words without punctuations."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ],
        max_tokens=100
    )

    caption = response.choices[0].message.content

    #print("Caption:", caption)

    try:
        # Connect to Azure SQL
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()

        print("Connected to Azure SQL!")

        # Example insert query
        insert_query = """
        INSERT INTO photos_v2 (name, caption,area_location,image_url,ai_generated_caption)
        VALUES (?,?,?,?,?)
        """

        # Data to insert
        data = [
            (request.form.get('name'), request.form.get('caption'),request.form.get('area_location'),blob_client.url,caption)
        ]

        # Execute multiple inserts
        cursor.executemany(insert_query, data)

        # Commit changes
        conn.commit()

        print(f"{cursor.rowcount} records inserted successfully!")

    except Exception as e:
        print("Error:", str(e))

    finally:
        if 'conn' in locals():
            conn.close()

    return jsonify({
        "image_url": blob_client.url,
        "name" : request.form.get('name'),
         "caption" : request.form.get('caption'),
         "area_location" : request.form.get('area_location')
    })

@app.route("/gallery", methods=["GET"])
def view_gallery():
    CONTAINER_SAS_URL = "https://picturesupload.blob.core.windows.net/images?sp=racwdl&st=2026-04-14T11:00:49Z&se=2026-12-31T19:15:49Z&sv=2025-11-05&sr=c&sig=fElH6pXI%2FoPolOuvI%2Bj2wFB%2BV9TPfNeBa7PxRBQkD0U%3D"

    container_client = ContainerClient.from_container_url(CONTAINER_SAS_URL)

    blobs = container_client.list_blob_names()
    image_extensions = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")

    base_url = CONTAINER_SAS_URL.split("?")[0]
    sas_token = CONTAINER_SAS_URL.split("?")[1]

    img_url = []

    for blob in blobs:
        if blob.lower().endswith(image_extensions):
            full_url = f"{base_url}/{blob.split('/')[-1]}?{sas_token}"
            img_url.append(full_url)
    
    return (img_url)

@app.route("/search", methods=["POST"])
def search_gallery():
    data = request.get_json()

    if not data or "query" not in data:
        return jsonify({"error": "Missing 'query' field"}), 400

    query_text = data["query"]

    # Step 1: Convert text to embedding
    embedding = generate_text_embedding(query_text)

    # Step 2: Perform vector search
    vector_query = VectorizedQuery(
    vector=embedding,
    k_nearest_neighbors=1,
    fields="vector"
   )

    results = search_client.search(
        search_text=None,
        vector_queries=[vector_query]
    )

    top_result = next(results, None)

    if not top_result:
        return jsonify({"message": "No images found"}), 404

    return jsonify({
        "imageUrl": top_result["imageUrl"],
        "score": top_result["@search.score"]
    })

@app.route("/find", methods=["POST"])
def find_image():
    data = request.get_json()

    if not data or "query" not in data:
        return jsonify({"error": "Missing 'query' field"}), 400

    query_text = data["query"]

    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()

    select_query = "SELECT * FROM photos WHERE name = ?"
    #top_result=cursor.execute(select_query, query_text).fetchone()[3]

    rows = cursor.execute(select_query, query_text).fetchall()
    top_result = [row[3] for row in rows]

    if not top_result:
        return jsonify({"message": "No images found"}), 404

    return jsonify({
        "imageUrl": top_result
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)


