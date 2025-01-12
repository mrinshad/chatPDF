from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Dict
import os
import uuid
from unstructured_ingest.v2.pipeline.pipeline import Pipeline
from unstructured_ingest.v2.interfaces import ProcessorConfig
from unstructured_ingest.v2.processes.connectors.local import (
    LocalIndexerConfig,
    LocalDownloaderConfig,
    LocalConnectionConfig,
    LocalUploaderConfig
)
from unstructured_ingest.v2.processes.partitioner import PartitionerConfig
from fastapi.middleware.cors import CORSMiddleware 
from dotenv import load_dotenv
import google.generativeai as genai

from models import User
import utils

# Load environment variables
load_dotenv()

# Get environment variables
UNSTRUCTUREDIO_API_KEY = os.getenv('UNSTRUCTUREDIO_API_KEY')
UNSTRUCTUREDIO_ENDPOINT = os.getenv('UNSTRUCTUREDIO_ENDPOINT')
UPLOAD_DIRECTORY = "./uploaded_documents"
OUTPUT_DIRECTORY = "./output"
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', 'http://localhost:3000').split(',')

# Create directories if they don't exist
os.makedirs(OUTPUT_DIRECTORY, exist_ok=True)
os.makedirs(UPLOAD_DIRECTORY, exist_ok=True)

# Initialize Gemini model
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# Initialize FastAPI
app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize document storage
documents_db = {}

class DocumentQuery(BaseModel):
    document_id: str
    query: str

def process_document_with_unstructured(file_path: str) -> str:
    """Process document using unstructured.io pipeline"""
    try:
        Pipeline.from_configs(
            context=ProcessorConfig(),
            indexer_config=LocalIndexerConfig(input_path=file_path),
            downloader_config=LocalDownloaderConfig(),
            source_connection_config=LocalConnectionConfig(),
            partitioner_config=PartitionerConfig(
                partition_by_api=True,
                api_key= UNSTRUCTUREDIO_API_KEY,
                partition_endpoint= UNSTRUCTUREDIO_ENDPOINT,
                strategy="hi_res",
                additional_partition_args={
                    "split_pdf_page": True,
                    "split_pdf_allow_failed": True,
                    "split_pdf_concurrency_level": 15
                }
            ),
            uploader_config=LocalUploaderConfig(output_dir=OUTPUT_DIRECTORY)
        ).run()
        
        # Read the processed output
        output_file = os.path.join(OUTPUT_DIRECTORY, os.path.basename(file_path)) +'.json'
        if os.path.exists(output_file):
            with open(output_file, 'r', encoding='utf-8') as f:
                return f.read()
        return ""
    except Exception as e:
        raise Exception(f"Error processing document: {str(e)}")

def query_document_with_gemini(document_content: str, query: str) -> str:
    """Process query using Gemini model"""
    try:

        # Create a prompt that combines the document content and query
        prompt = f"""
        Based on the following document content, please answer the question.
        
        Document content:
        {document_content}
        
        Question: {query}
        
        Please provide a concise and accurate answer based only on the information provided in the document.
        """
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        raise Exception(f"Error processing query with Gemini: {str(e)}")

# Rest of the endpoints remain the same...
@app.post("/signup")
async def sign_up(user: User):
    try:
        response = utils.create_user(user.email, user.password)
        
        if not response or response.confirmed_at is None:
            raise HTTPException(status_code=400, detail="User creation failed.")
        
        return JSONResponse(content={
            "message": "User created successfully",
            "data": {
                "id": response.id,
                "email": response.email,
                "created_at": response.created_at.isoformat(),
            }
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """Upload and process a document"""
    file_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIRECTORY, f"{file_id}_{file.filename}")
    
    try:
        with open(file_path, "wb") as f:
            f.write(await file.read())
        
        processed_content = process_document_with_unstructured(file_path)
        
        documents_db[file_id] = {
            "file_name": file.filename,
            "file_path": file_path,
            "content": processed_content,
        }
        
        return JSONResponse(content={
            "message": "Document uploaded and processed successfully",
            "document_id": file_id
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ask")
async def ask_question(query: DocumentQuery):
    """Ask a question about a specific document"""
    if query.document_id not in documents_db:
        raise HTTPException(status_code=404, detail="Document not found")
    
    try:
        document_content = documents_db[query.document_id]["content"]
        response = query_document_with_gemini(document_content, query.query)
        
        return JSONResponse(content={
            "query": query.query,
            "response": response,
            "document": documents_db[query.document_id]["file_name"]
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/documents")
async def list_documents():
    """List all uploaded documents"""
    return [{
        "document_id": doc_id,
        "file_name": info["file_name"]
    } for doc_id, info in documents_db.items()]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)