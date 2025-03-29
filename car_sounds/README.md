# Motor Sound Detection API

This application provides a FastAPI service that classifies motor sounds using a pre-trained model. It integrates with RabbitMQ to process audio files asynchronously.

## Features

- 🎛️ Classifies audio files into motor sound categories: fan, gearbox, pump, valve
- 📨 Processes audio files from a RabbitMQ message queue
- 🔄 Returns classification results to a response queue
- 📊 Provides confidence scores for each classification
- 🛠️ Includes API endpoints for direct uploads and health checks

## Prerequisites

- Docker and Docker Compose
- The pre-trained motor sound model (`motorsoundsmodel`) from your Python notebook

## Quick Start

1. **Clone the repository**

2. **Copy your model files**

   Place your trained `motorsoundsmodel` files in a `models` directory:

   ```
   mkdir -p models
   cp /path/to/your/motorsoundsmodel* models/
   ```

3. **Start the application with Docker Compose**

   ```
   docker-compose up --build
   ```

4. **Access the API**

   The API will be available at http://localhost:8000

   - API documentation: http://localhost:8000/docs
   - Health check: http://localhost:8000/health

5. **RabbitMQ Management Console**

   Access the RabbitMQ management interface at http://localhost:15672
   - Username: guest
   - Password: guest

## Using the API

### Direct File Upload

You can upload an audio file directly to the API for processing:

```bash
curl -X POST "http://localhost:8000/upload/" -H "accept: application/json" -H "Content-Type: multipart/form-data" -F "file=@/path/to/your/audiofile.wav"
```

### Send File Path to Queue

Send a file path to be processed via the queue:

```bash
curl -X POST "http://localhost:8000/send-to-queue/?file_path=/path/to/your/audiofile.wav" -H "accept: application/json"
```

### Using the Example Client

The included client example demonstrates how to send files to the processing queue and receive results:

```bash
# Send a file for processing
python client_example.py --file /path/to/your/audiofile.wav

# Just listen for all processing results
python client_example.py --listen
```

## Configuration

The application can be configured using environment variables:

- `MODEL_PATH`: Path to the motor sound model (default: "motorsoundsmodel")
- `MODEL_TYPE`: Model type (default: "gradientboosting")
- `RABBITMQ_URL`: RabbitMQ connection URL (default: "amqp://guest:guest@localhost/")
- `REQUEST_QUEUE`: Name of the request queue (default: "audio_processing_requests")
- `RESPONSE_QUEUE`: Name of the response queue (default: "audio_processing_results")

These can be modified in the `docker-compose.yml` file.

## Architecture

This application follows a message-driven architecture:

1. Audio files are sent to a request queue
2. The API service processes files from the queue
3. Classification results are sent to a response queue
4. Clients can listen to the response queue for results

This allows for scalable, asynchronous processing of audio files.

## Extending the Application

To add more functionality:

- **Support for more audio formats**: Add audio format conversion using `pydub` or `ffmpeg`
- **Authentication**: Add API authentication using FastAPI's security features
- **Result storage**: Add a database to store processing results
- **Scaling**: Deploy multiple instances of the API service to process files in parallel

## Troubleshooting

- **Model loading issues**: Ensure the model files are correctly placed in the models directory
- **RabbitMQ connection problems**: Check the RabbitMQ logs and ensure the service is running
- **Audio processing errors**: Verify the audio file format is supported by pyAudioAnalysis