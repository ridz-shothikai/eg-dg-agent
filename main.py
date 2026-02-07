import os
import sys
import uuid
import shutil
import json

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from fastapi import BackgroundTasks
from tenacity import retry, stop_after_attempt, wait_fixed

import vertexai
from vertexai.preview import reasoning_engines
from BOQ_development_agent.agent import root_agent

from pymongo import MongoClient

REQUIRED_COMPONENTS = [
    "component_geometry", "pile_details", "reinforcement_details",
    "material_specs", "seismic_arrestors", "structural_notes",
    "compliance_parameters", "boq"
]



# from storing_data import get_component_from_db, store_component_in_db



# 1. Load and validate environment variables
load_dotenv()
project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
location = os.getenv("GOOGLE_CLOUD_LOCATION")
from pathlib import Path
print(f"Loaded .env from: {Path('.env').resolve()}")
print(f"GOOGLE_CLOUD_PROJECT={os.getenv('GOOGLE_CLOUD_PROJECT')}")


if not project_id or not location:
    print("Missing required environment variable: GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_LOCATION")
    sys.exit(1)
    
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGODB_URI)
db = client["engineering_components"]

def store_component_in_db(collection_name, component_data, user_id, session_id,status):
    collection = db[collection_name]

    # Determine status
    # if component_data:
    #     status = "completed"
    # else:
    #     # Check if document already exists → skip insert if already inserted as "pending"
    #     if collection.find_one({"user_id": user_id, "session_id": session_id}):
    #         return  # Avoid duplicate
    #     status = "pending"

    doc = {
        "user_id": user_id,
        "session_id": session_id,
        "data": component_data,
        "status": status
    }

    result = collection.insert_one(doc)
    print(f"📥 Stored in '{collection_name}' with _id: {result.inserted_id}, status: {status}")

def get_component_from_db(collection_name, user_id, session_id):
    collection = db[collection_name]
    result = collection.find_one({"user_id": user_id, "session_id": session_id})
    if result:
        return {
            "status": result.get("status", "unknown"),
            "data": result.get("data", {})
        }
    else:
        return {
            "status": "pending",
            "data": []
        }
    
@retry(stop=stop_after_attempt(3), wait=wait_fixed(60))
def process_file_in_background(user_id: str, session_id: str, file_path: str):
    # from storing_data import store_component_in_db
    # import json
    # import os
    parsed_components = []

    boq_data = None
    validation_result = None
    validdation_count = 0

    try:
        app_stream = app_instance.stream_query(
            user_id=user_id,
            session_id=session_id,
            message=f"[FILE] {file_path}",
        )

        for event in app_stream:
            try:
                part_text = event["content"]["parts"][0]["text"]

                if part_text.startswith("```json"):
                    part_text = part_text.replace("```json", "").strip()
                if part_text.endswith("```"):
                    part_text = part_text[:-3].strip()

                print("✅ Cleaned JSON text:", part_text)

                parsed = json.loads(part_text)

                if "boq" in parsed:
                    boq_data = parsed["boq"]
                    print("📦 Captured BoQ data")

                if "validation" in parsed:
                    validdation_count+=1
                    validation_result = parsed
                    print("🧪 Captured validation result:", validation_result)

                    if validation_result.get("validation") == "pass" and boq_data:
                        store_component_in_db("boq", boq_data, user_id, session_id,status="completed")
                        parsed_components.append("boq")
                        break
                    elif "validation" in parsed and validdation_count ==3:
                        print("❗️ Validation failed after 3 attempts, stopping stream.")
                        store_component_in_db("boq", boq_data, user_id, session_id,status="completed")
                        parsed_components.append("boq")
                        break
                    continue
                for key in REQUIRED_COMPONENTS:
                    if key in parsed and key not in parsed_components and key != "boq":
                        store_component_in_db(key, parsed[key], user_id, session_id,status="completed")
                        parsed_components.append(key)
                        print(f"✅ Stored component: {key}")      

            except Exception as e:
                print(f"❌ Stream event processing failed: {e}")

    except Exception as e:
        print(f"⛔ Final failure after retries: {e}")
        # ROLLBACK by manually deleting all previously stored docs for session
        for key in REQUIRED_COMPONENTS:
            if key not in parsed_components:
                print(f"🗑️ Rolling back {key} for user_id={user_id}, session_id={session_id}")
                #update the status failed for the component that are not parsed in the database
                store_component_in_db(key, [], user_id, session_id,status="failed")
                
            # deleted = db[key].delete_many({
            #     "user_id": user_id,
            #     "session_id": session_id
            # })
            # if deleted.deleted_count > 0:
            #     print(f"🗑️ Rolled back {deleted.deleted_count} docs in {key}")

    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"🧹 Deleted temporary file: {file_path}")
        except Exception as cleanup_err:
            print(f"⚠️ Failed to delete temp file: {cleanup_err}")


# 2. Initialize Vertex AI & ADK app
vertexai.init(project=project_id, location=location)
app_instance = reasoning_engines.AdkApp(agent=root_agent, enable_tracing=False)

# 3. Create FastAPI app
app = FastAPI(
    title="ADK + FastAPI Server",
    root_path=os.getenv("ROOT_PATH", "")
)


# -- Data models -------------------------------------------------------------
class QueryInput(BaseModel):
    user_id: str
    session_id: str
    message: str


# -- Endpoints ---------------------------------------------------------------

@app.post("/create_session/")
async def create_session(user_id: str):
    """
    Create a new ADK session for the given user_id.
    """
    try:
        session = app_instance.create_session(user_id=user_id)
        return {
            "session_id": session.id,
            "user_id": session.user_id,
            "app_name": session.app_name,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/list_sessions/")
async def list_sessions(user_id: str):
    """
    List all sessions for the given user_id.
    """
    try:
        sessions = app_instance.list_sessions(user_id=user_id)
        if hasattr(sessions, "sessions"):
            return {"sessions": sessions.sessions}
        elif hasattr(sessions, "session_ids"):
            return {"session_ids": sessions.session_ids}
        else:
            return {"raw": str(sessions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/")
async def stream_query(input: QueryInput):
    """
    Send a text message into an existing session and return the aggregated response.
    """
    try:
        response = ""
        for event in app_instance.stream_query(
            user_id=input.user_id,
            session_id=input.session_id,
            message=input.message,
        ):
            if hasattr(event, "text"):
                response += event.text
        return {"response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    
@app.post("/upload/")
async def upload_file(
    background_tasks: BackgroundTasks,
    user_id: str = Form(...),
    file: UploadFile = File(...),
):
    """
    Upload a PDF and trigger background processing.
    Returns only the session_id immediately.
    """
    # Save file
    upload_dir = "uploaded_files"
    os.makedirs(upload_dir, exist_ok=True)
    unique_name = f"{uuid.uuid4()}_{file.filename}"
    file_path = os.path.join(upload_dir, unique_name)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    # Create ADK session
    try:
        session = app_instance.create_session(user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create session: {e}")

    # Kick off background task
    background_tasks.add_task(process_file_in_background, user_id, session.id, file_path)

    return {
        "status": "processing",
        "session_id": session.id,
    }





@app.get("/component_geometry")
async def get_component_geometry(user_id: str, session_id: str):
    return get_component_from_db("component_geometry", user_id, session_id)

@app.get("/pile_details")
async def get_pile_details(user_id: str, session_id: str):
    return get_component_from_db("pile_details", user_id, session_id)

@app.get("/reinforcement_details")
async def get_reinforcement_details(user_id: str, session_id: str):
    return get_component_from_db("reinforcement_details", user_id, session_id)

@app.get("/material_specs")
async def get_material_specs(user_id: str, session_id: str):
    return get_component_from_db("material_specs", user_id, session_id)

@app.get("/seismic_arrestors")
async def get_seismic_arrestors(user_id: str, session_id: str):
    return get_component_from_db("seismic_arrestors", user_id, session_id)

@app.get("/structural_notes")
async def get_structural_notes(user_id: str, session_id: str):
    return get_component_from_db("structural_notes", user_id, session_id)

@app.get("/compliance_parameters")
async def get_compliance_parameters(user_id: str, session_id: str):
    return get_component_from_db("compliance_parameters", user_id, session_id)

@app.get("/boq")
async def get_boq(user_id: str, session_id: str):
    return get_component_from_db("boq", user_id, session_id)

# -- Run with uvicorn -------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8060, reload=True)
