source venv/Scripts/activate
export GOOGLE_APPLICATION_CREDENTIALS="service-account.json"
# uvicorn main:app --host 0.0.0.0 --port 8010 --reload
gunicorn main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8010
