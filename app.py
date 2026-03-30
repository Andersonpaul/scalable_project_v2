from flask import Flask, request, jsonify
from flask_cors import CORS
from azure.storage.blob import BlobServiceClient, ContainerClient
import os
import requests
import openai
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.models import VectorizedQuery


app = Flask(__name__)
CORS(app)

# Use environment variable in production
AZURE_CONNECTION_STRING = "BlobEndpoint=https://picturesupload.blob.core.windows.net/;QueueEndpoint=https://picturesupload.queue.core.windows.net/;FileEndpoint=https://picturesupload.file.core.windows.net/;TableEndpoint=https://picturesupload.table.core.windows.net/;SharedAccessSignature=sv=2024-11-04&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2050-03-30T22:45:30Z&st=2026-03-30T14:30:30Z&spr=https,http&sig=2AcrZW6JpKeLjnv8IHwbeWrC9nkmsp1jgXau9gZ5Gjo%3D"
CONTAINER_NAME = "images"
openai.api_key = os.getenv("OPENAI_API_KEY")
SEARCH_ENDPOINT = "https://cw2aivision.search.windows.net"
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY")
SEARCH_INDEX = "image-index"

search_client = SearchClient(
    endpoint=SEARCH_ENDPOINT,
    index_name=SEARCH_INDEX,
    credential=AzureKeyCredential(SEARCH_KEY)
)

blob_service_client = BlobServiceClient.from_connection_string(
    AZURE_CONNECTION_STRING
)

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

    return jsonify({
        "image_url": blob_client.url
    })

@app.route("/gallery", methods=["GET"])
def view_gallery():
    CONTAINER_SAS_URL = "https://picturesupload.blob.core.windows.net/images?sp=r&st=2026-03-30T14:38:05Z&se=2050-03-30T22:53:05Z&sv=2024-11-04&sr=c&sig=Uo8dUBl0jR7clmX%2BtdOLjFVSkJYTASuRKsikwlDuRf8%3D"

    container_client = ContainerClient.from_container_url(CONTAINER_SAS_URL)

    blobs = container_client.list_blob_names()
    image_extensions = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")

    base_url = CONTAINER_SAS_URL.split("?")[0]
    sas_token = CONTAINER_SAS_URL.split("?")[1]

    img_url = []

    for blob in blobs:
        if blob.lower().endswith(image_extensions):
            full_url = f"{base_url}/images/{blob.split('/')[-1]}?{sas_token}"
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
