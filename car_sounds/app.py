"""
FastAPI application for motor sound detection without Docker.
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
initialization_complete = asyncio.Event()

async def get_rabbitmq_channel():
    """Get or create a RabbitMQ channel."""
    global rabbitmq_connection, rabbitmq_channel
    
    # Wait for initialization to complete
    await initialization_complete.wait()
    
    async with connection_lock:
        # Check if connection needs to be established or re-established
        if rabbitmq_connection is None or rabbitmq_connection.is_closed:
            logger.info(f"Creating new RabbitMQ connection to {RABBITMQ_URL}")
            rabbitmq_connection = await aio_pika.connect_robust(RABBITMQ_URL)
            
        # Check if channel needs to be created or re-created
        if rabbitmq_channel is None or rabbitmq_channel.is_closed:
            logger.info("Creating new RabbitMQ channel")
            rabbitmq_channel = await rabbitmq_connection.channel()
            
            # Declare queues
            await rabbitmq_channel.declare_queue(REQUEST_QUEUE, durable=True)
            await rabbitmq_channel.declare_queue(RESPONSE_QUEUE, durable=True)
    
    return rabbitmq_channel

async def init_rabbitmq():
    """Initialize RabbitMQ connection and channel."""
    try:
        logger.info(f"Initializing RabbitMQ connection to {RABBITMQ_URL}")
        channel = await get_rabbitmq_channel()
        logger.info("RabbitMQ initialization completed successfully")
        initialization_complete.set()  # Signal that initialization is complete
        return channel
    except Exception as e:
        logger.error(f"Failed to initialize RabbitMQ: {e}")
        # Set the event anyway to prevent hanging, but with a short delay for retries
        await asyncio.sleep(5)
        initialization_complete.set()
        raise

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

async def process_queue_messages():
    """Process messages from the request queue."""
    while True:
        try:
            # Get a channel for consuming messages
            channel = await get_rabbitmq_channel()
            
            # Declare the queue and start consuming
            queue = await channel.declare_queue(REQUEST_QUEUE, durable=True)
            
            logger.info(f"Started listening to the {REQUEST_QUEUE} queue")
            
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    async with message.process():
                        try:
                            logger.debug(f"Received message: {message.body.decode()}")
                            # Parse message content
                            message_data = json.loads(message.body.decode())
                            message_id = message_data.get("message_id", str(uuid.uuid4()))
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
        except asyncio.CancelledError:
            logger.info("Queue processing task cancelled")
            break
        except Exception as e:
            logger.error(f"Queue processing error: {e}")
            # Sleep before retrying
            await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    """Initialize connections and start queue processing on startup."""
    try:
        logger.info("Starting application...")
        
        # Initialize RabbitMQ
        await init_rabbitmq()
        
        # Start the queue processing task in the background
        asyncio.create_task(process_queue_messages())
        
        logger.info("Application startup complete")
    except Exception as e:
        logger.critical(f"Startup failed: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Close connections on shutdown."""
    global rabbitmq_connection
    logger.info("Shutting down application...")
    if rabbitmq_connection:
        await rabbitmq_connection.close()
        logger.info("RabbitMQ connection closed")

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
async def send_to_queue(file_path: str, channel: aio_pika.Channel = Depends(get_rabbitmq_channel)):
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
        if not initialization_complete.is_set():
            return {
                "status": "initializing",
                "message": "RabbitMQ connection is still being initialized"
            }
            
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
            # Try to re-establish connection
            try:
                logger.info("Attempting to re-establish RabbitMQ connection")
                await init_rabbitmq()
                return {
                    "status": "reconnected",
                    "rabbitmq_url": RABBITMQ_URL,
                    "message": "RabbitMQ connection re-established"
                }
            except Exception as e:
                return {
                    "status": "disconnected",
                    "rabbitmq_url": RABBITMQ_URL,
                    "message": f"Failed to re-connect: {str(e)}"
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
        "rabbitmq_url": RABBITMQ_URL,
        "initialization_complete": initialization_complete.is_set()
    }

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
