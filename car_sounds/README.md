# Running the Application Locally

## Prerequisites

1. Make sure RabbitMQ is installed and running on your local machine
2. Python 3.8+ is installed
3. You have your trained motor sound model files

## Step 1: Set Up Python Environment

```bash
# Create a virtual environment
python -m venv venv

# Activate it (Windows)
venv\Scripts\activate

# Activate it (Linux/Mac)
# source venv/bin/activate
```

## Step 2: Install Dependencies

```bash
pip install fastapi uvicorn aio-pika python-multipart pyAudioAnalysis eyed3 hmmlearn numpy pydub imbalanced-learn scikit-learn
```

## Step 3: Project Setup

1. Create project directories:
   ```bash
   mkdir -p models
   mkdir -p test_audio
   ```

2. Copy your model files into the `models` directory.

3. Copy some test audio files into the `test_audio` directory.

## Step 4: Run the Application

```bash
# Start the FastAPI application
python app.py
```

The application will start on http://localhost:8000.

## Step 5: Testing

### Using Postman

1. Test the API health:
   - GET http://localhost:8000/health/

2. Check RabbitMQ status:
   - GET http://localhost:8000/rabbitmq-status/

3. Check if a file is accessible:
   - GET http://localhost:8000/file-check/?file_path=C:/path/to/your/test_audio/test.wav

4. Send a file to processing:
   - POST http://localhost:8000/send-to-queue/?file_path=C:/path/to/your/test_audio/test.wav

### Using the Client Example

```bash
# Send a file for processing and wait for the result
python client_local.py --file C:/path/to/your/test_audio/test.wav

# Just listen for all processing results
python client_local.py --listen
```

## Troubleshooting

1. If you get connection errors:
   - Verify RabbitMQ is running with `rabbitmqctl status`
   - Try restarting RabbitMQ

2. If file paths aren't working:
   - Make sure to use absolute paths
   - On Windows, use forward slashes or escaped backslashes in URLs

3. If the model isn't loading:
   - Check the model path in `app.py`
   - Make sure all model files are in the `models` directory

4. If you get "No module found" errors:
   - Make sure you've installed all the required dependencies
   - Try installing them one by one to identify any problematic packages