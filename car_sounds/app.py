"""
FastAPI application for motor sound detection - with lazy RabbitMQ initialization.
"""

import os
import tempfile
import uuid
import json
import aio_pika
import asyncio
import uvicorn
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from pyAudioAnalysis import audioTrainTest as aT
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Initialize FastAPI application
app = FastAPI(
    title="Motor Sound Detection API",
    description="API for detecting types of motor sounds using audio analysis",
    version="1.0.0"
)

# Configuration - adjusted for local execution
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "motorsoundsmodel")
MODEL_TYPE = "gradientboosting"
RABBITMQ_URL = "amqp://guest:guest@localhost:5672/"  # Local RabbitMQ
REQUEST_QUEUE = "audio_processing_requests"
RESPONSE_QUEUE = "audio_processing_results"

# Create necessary directories
os.makedirs(os.path.join(os.path.dirname(__file__), "models"), exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(__file__), "test_audio"), exist_ok=True)

# Connection management
rabbitmq_connection = None
rabbitmq_channel = None
connection_lock = asyncio.Lock()

# Remove the initialization_complete event since we're using lazy initialization

async def get_rabbitmq_channel():
    """Get or create a RabbitMQ channel using lazy initialization."""
    global rabbitmq_connection, rabbitmq_channel
    
    async with connection_lock:
        # Check if connection needs to be established or re-established
        if rabbitmq_connection is None or rabbitmq_connection.is_closed:
            logger.info(f"Creating new RabbitMQ connection to {RABBITMQ_URL}")
            try:
                rabbitmq_connection = await asyncio.wait_for(
                    aio_pika.connect_robust(RABBITMQ_URL),
                    timeout=5.0  # 5 second timeout
                )
            except asyncio.TimeoutError:
                logger.error("Timeout connecting to RabbitMQ")
                raise Exception("Timeout connecting to RabbitMQ")
            
        # Check if channel needs to be created or re-created
        if rabbitmq_channel is None or rabbitmq_channel.is_closed:
            logger.info("Creating new RabbitMQ channel")
            rabbitmq_channel = await rabbitmq_connection.channel()
            
            # Declare queues
            await rabbitmq_channel.declare_queue(REQUEST_QUEUE, durable=True)
            await rabbitmq_channel.declare_queue(RESPONSE_QUEUE, durable=True)
    
    return rabbitmq_channel

# Remove the init_rabbitmq function, as it's now integrated into get_rabbitmq_channel

# Remove the startup event that was causing issues

@app.on_event("shutdown")
async def shutdown_event():
    """Close connections on shutdown."""
    global rabbitmq_connection
    logger.info("Shutting down application...")
    if rabbitmq_connection:
        await rabbitmq_connection.close()
        logger.info("RabbitMQ connection closed")

async def process_audio_file(file_path, message_id):
    """Process an audio file and classify the motor sound."""
    try:
        logger.info(f"Processing audio file: {file_path}")
        
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
        # Get a channel for sending the result
        channel = await get_rabbitmq_channel()
        
        logger.info(f"Sending result to queue {RESPONSE_QUEUE}")
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

# Remove process_queue_messages for now, to simplify the initial setup

@app.post("/upload/", response_class=JSONResponse)
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    Endpoint to manually upload an audio file for processing.
    This is useful for testing or direct API usage.
    """
    try:
        logger.info(f"Received file upload: {file.filename}")
        # Generate a unique ID for this request
        message_id = str(uuid.uuid4())
        
        # Create a temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temp_file.write(await file.read())
            temp_file_path = temp_file.name
        
        logger.debug(f"Saved uploaded file to: {temp_file_path}")
        
        # Process the file in the background
        background_tasks.add_task(process_audio_file, temp_file_path, message_id)
        
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
        logger.info(f"Request to queue file: {file_path}")
        
        # Verify file exists
        if not os.path.exists(file_path):
            logger.warning(f"File not found: {file_path}")
            return JSONResponse(
                status_code=404,
                content={
                    "error": f"File not found: {file_path}",
                    "status": "failed"
                }
            )
        
        message_id = str(uuid.uuid4())
        message = {
            "message_id": message_id,
            "file_path": file_path
        }
        
        logger.debug(f"Sending message to queue: {message}")
        
        # Now we get the channel lazily when needed
        channel = await get_rabbitmq_channel()
        
        # Add timeout to prevent hanging
        try:
            await asyncio.wait_for(
                channel.default_exchange.publish(
                    aio_pika.Message(
                        body=json.dumps(message).encode(),
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                    ),
                    routing_key=REQUEST_QUEUE
                ),
                timeout=5.0  # 5 second timeout
            )
            
            logger.info(f"Message sent to queue successfully for file: {file_path}")
            
            return {
                "message": "Request sent to processing queue",
                "message_id": message_id,
                "file_path": file_path,
                "status": "queued"
            }
        except asyncio.TimeoutError:
            logger.error("Timeout while sending message to queue")
            return JSONResponse(
                status_code=504,
                content={"error": "Timeout while sending to queue", "status": "failed"}
            )
    
    except Exception as e:
        logger.error(f"Error sending to queue: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "status": "failed"}
        )

@app.get("/test/")
async def test_endpoint():
    """Simple test endpoint to verify API is responding."""
    logger.info("Test endpoint called")
    return {"status": "API is working"}

@app.get("/rabbitmq-status/")
async def rabbitmq_status():
    """Check RabbitMQ connection status."""
    global rabbitmq_connection, rabbitmq_channel
    
    try:
        if rabbitmq_connection and not rabbitmq_connection.is_closed:
            return {
                "status": "connected",
                "rabbitmq_url": RABBITMQ_URL,
                "connection_info": {
                    "connected": True,
                    "closed": rabbitmq_connection.is_closed,
                }
            }
        else:
            # Try to establish a connection
            try:
                channel = await get_rabbitmq_channel()
                return {
                    "status": "connected",
                    "rabbitmq_url": RABBITMQ_URL,
                    "message": "RabbitMQ connection established"
                }
            except Exception as e:
                return {
                    "status": "disconnected",
                    "rabbitmq_url": RABBITMQ_URL,
                    "message": f"Failed to connect: {str(e)}"
                }
    except Exception as e:
        logger.error(f"Error checking RabbitMQ status: {e}")
        return {
            "status": "error",
            "rabbitmq_url": RABBITMQ_URL,
            "error": str(e)
        }

@app.get("/file-check/")
async def file_check(file_path: str):
    """Check if a file exists and is accessible."""
    try:
        logger.info(f"Checking file: {file_path}")
        if os.path.exists(file_path):
            file_stats = os.stat(file_path)
            return {
                "exists": True,
                "file_path": file_path,
                "size_bytes": file_stats.st_size,
                "permissions": oct(file_stats.st_mode)[-3:],
                "is_readable": os.access(file_path, os.R_OK)
            }
        else:
            return {
                "exists": False,
                "file_path": file_path
            }
    except Exception as e:
        logger.error(f"Error checking file: {e}")
        return {
            "error": str(e),
            "file_path": file_path
        }

@app.get("/health/")
async def health_check():
    """Health check endpoint."""
    logger.info("Health check called")
    return {
        "status": "healthy", 
        "model_path": MODEL_PATH, 
        "model_type": MODEL_TYPE,
        "rabbitmq_url": RABBITMQ_URL
    }

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Motor Sound Detection API is running"}

if __name__ == "__main__":
    print("Starting server on port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
