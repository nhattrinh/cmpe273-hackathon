"""
FastAPI application for motor sound detection.
This app consumes audio files from a message queue, processes them using a 
pre-trained motor sound classification model, and returns the results.
"""

import os
import tempfile
import uuid
import json
import aio_pika
import asyncio
import uvicorn
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse
from pyAudioAnalysis import audioTrainTest as aT
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI application
app = FastAPI(
    title="Motor Sound Detection API",
    description="API for detecting types of motor sounds using audio analysis",
    version="1.0.0"
)

# Configuration
MODEL_PATH = os.environ.get("MODEL_PATH", "motorsoundsmodel")
MODEL_TYPE = os.environ.get("MODEL_TYPE", "gradientboosting")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost/")
REQUEST_QUEUE = os.environ.get("REQUEST_QUEUE", "audio_processing_requests")
RESPONSE_QUEUE = os.environ.get("RESPONSE_QUEUE", "audio_processing_results")

# Global connection
connection = None
channel = None


async def init_rabbitmq():
    """Initialize RabbitMQ connection and channels."""
    global connection, channel
    try:
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        channel = await connection.channel()

        # Declare queues
        await channel.declare_queue(REQUEST_QUEUE, durable=True)
        await channel.declare_queue(RESPONSE_QUEUE, durable=True)

        logger.info("RabbitMQ connection established")
    except Exception as e:
        logger.error(f"Failed to initialize RabbitMQ: {e}")
        raise


async def process_audio_file(file_path, message_id):
    """Process an audio file and classify the motor sound."""
    try:
        # Use the pre-trained model to classify the audio file
        c, probabilities, class_names = aT.file_classification(
            file_path, 
            MODEL_PATH, 
            MODEL_TYPE
        )

        # Convert numpy arrays to Python lists for JSON serialization
        probabilities_list = probabilities.tolist()

        # Find the predicted class
        max_index = probabilities.argmax()
        predicted_class = class_names[max_index]
        confidence = float(probabilities[max_index])

        result = {
            "message_id": message_id,
            "predicted_class": predicted_class,
            "confidence": round(confidence, 5),
            "all_probabilities": dict(zip(class_names, probabilities_list))
        }

        # Log the result
        logger.info(f"Processed file {file_path}: {predicted_class} (confidence: {confidence:.5f})")

        # Send result to response queue
        await send_result_to_queue(result)

        return result

    except Exception as e:
        logger.error(f"Error processing audio file: {e}")
        error_result = {
            "message_id": message_id,
            "error": str(e),
            "status": "failed"
        }
        await send_result_to_queue(error_result)
        return error_result


async def send_result_to_queue(result):
    """Send processing result to the response queue."""
    try:
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(result).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key=RESPONSE_QUEUE
        )
        logger.info(f"Result sent to queue {RESPONSE_QUEUE}")
    except Exception as e:
        logger.error(f"Failed to send result to queue: {e}")


async def process_queue_messages():
    """Process messages from the request queue."""
    try:
        async with connection.channel() as channel:
            queue = await channel.declare_queue(REQUEST_QUEUE, durable=True)

            async with queue.iterator() as queue_iter:
                logger.info(f"Started listening to the {REQUEST_QUEUE} queue")

                async for message in queue_iter:
                    async with message.process():
                        try:
                            # Parse message content
                            message_data = json.loads(message.body.decode())
                            message_id = message_data.get(
                                "message_id",
                                str(uuid.uuid4())
                            )
                            file_path = message_data.get("file_path")

                            if not file_path or not os.path.exists(file_path):
                                logger.error(f"File not found: {file_path}")
                                await send_result_to_queue({
                                    "message_id": message_id,
                                    "error": f"File not found: {file_path}",
                                    "status": "failed"
                                })
                                continue
                      
                            # Process the audio file
                            await process_audio_file(file_path, message_id)
                 
                        except Exception as e:
                            logger.error(f"Error processing message: {e}")
    except Exception as e:
        logger.error(f"Queue processing error: {e}")
        # Attempt to reconnect
        await asyncio.sleep(5)
        await process_queue_messages()


@app.on_event("startup")
async def startup_event():
    """Initialize connections and start queue processing on startup."""
    try:
        await init_rabbitmq()
        # Start the queue processing task
        asyncio.create_task(process_queue_messages())
    except Exception as e:
        logger.error(f"Startup failed: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Close connections on shutdown."""
    global connection
    if connection:
        await connection.close()
        logger.info("RabbitMQ connection closed")


@app.post("/upload/", response_class=JSONResponse)
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    Endpoint to manually upload an audio file for processing.
    This is useful for testing or direct API usage.
    """
    try:
        # Generate a unique ID for this request
        message_id = str(uuid.uuid4())

        # Create a temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temp_file.write(await file.read())
            temp_file_path = temp_file.name

        # Process the file in the background
        background_tasks.add_task(
            process_audio_file, temp_file_path, message_id
        )

        return {
            "message": "File uploaded and processing started",
            "message_id": message_id,
            "file_name": file.filename,
            "status": "processing"
        }

    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "status": "failed"}
        )


@app.post("/send-to-queue/")
async def send_to_queue(file_path: str):
    """
    Endpoint to manually send a file path to the processing queue.
    This is useful for testing the queue functionality.
    """
    try:
        message_id = str(uuid.uuid4())
        message = {
            "message_id": message_id,
            "file_path": file_path
        }

        await channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(message).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key=REQUEST_QUEUE
        )

        return {
            "message": "Request sent to processing queue",
            "message_id": message_id,
            "file_path": file_path,
            "status": "queued"
        }

    except Exception as e:
        logger.error(f"Error sending to queue: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "status": "failed"}
        )


@app.get("/health/")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "model_path": MODEL_PATH,
        "model_type": MODEL_TYPE
    }

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
