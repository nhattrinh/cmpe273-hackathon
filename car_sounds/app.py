"""
FastAPI application for motor sound detection with RabbitMQ integration.
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
import sys
from datetime import datetime, timedelta

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

# Results storage with timestamps for expiration
results_storage = {}
RESULT_EXPIRY_HOURS = 24  # Results will expire after 24 hours

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
        # Fix the error by checking the type first
        if hasattr(probabilities, 'tolist'):
            probabilities_list = probabilities.tolist()
        else:
            # Handle the case where probabilities is an int or other type
            probabilities_list = [float(probabilities)]
            class_names = [str(class_names)]  # Ensure class_names is a list
        
        # Find the predicted class (safely)
        if hasattr(probabilities, 'argmax'):
            max_index = probabilities.argmax()
        else:
            max_index = 0  # If it's a single value, use index 0
            
        predicted_class = class_names[max_index] if max_index < len(class_names) else "unknown"
        confidence = float(probabilities[max_index] if hasattr(probabilities, '__getitem__') else probabilities)
        
        # Create a safe probability dictionary
        prob_dict = {}
        try:
            prob_dict = dict(zip(class_names, probabilities_list))
        except Exception as e:
            logger.warning(f"Could not create probability dictionary: {e}")
            prob_dict = {"unknown": confidence}
        
        result = {
            "message_id": message_id,
            "predicted_class": predicted_class,
            "confidence": round(float(confidence), 5),
            "all_probabilities": prob_dict,
            "status": "completed",
            "processed_at": datetime.now().isoformat()
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
            "status": "failed",
            "processed_at": datetime.now().isoformat()
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

async def start_response_queue_listener():
    """Start listening for messages on the response queue."""
    logger.info(f"Starting to listen for messages on {RESPONSE_QUEUE}")
    
    while True:
        try:
            # Get a channel
            channel = await get_rabbitmq_channel()
            
            # Declare the queue
            response_queue = await channel.declare_queue(RESPONSE_QUEUE, durable=True)
            
            logger.info(f"Successfully connected to {RESPONSE_QUEUE}, waiting for messages...")
            
            async with response_queue.iterator() as queue_iter:
                async for message in queue_iter:
                    async with message.process():
                        try:
                            # Parse the message content
                            content = json.loads(message.body.decode())
                            message_id = content.get("message_id")
                            
                            if message_id:
                                # Store the result by message ID with timestamp
                                results_storage[message_id] = {
                                    "result": content,
                                    "timestamp": datetime.now()
                                }
                                logger.info(f"Stored result for message ID: {message_id}")
                            
                            # Clean up expired results
                            await cleanup_expired_results()
                            
                        except Exception as e:
                            logger.error(f"Error processing response message: {e}")
        
        except asyncio.CancelledError:
            logger.info("Response queue listener cancelled")
            break
        except Exception as e:
            logger.error(f"Error in response queue listener: {e}")
            # Try to restart after a delay
            await asyncio.sleep(5)

async def cleanup_expired_results():
    """Remove expired results from storage."""
    now = datetime.now()
    expired_keys = []
    
    for key, value in results_storage.items():
        timestamp = value.get("timestamp")
        if timestamp and (now - timestamp) > timedelta(hours=RESULT_EXPIRY_HOURS):
            expired_keys.append(key)
    
    for key in expired_keys:
        del results_storage[key]
        logger.debug(f"Removed expired result for message ID: {key}")


async def start_request_queue_listener():
    """Start listening for messages on the request queue."""
    logger.info(f"Starting to listen for messages on {REQUEST_QUEUE}")
    
    while True:
        try:
            # Get a channel
            channel = await get_rabbitmq_channel()
            
            # Declare the queue
            request_queue = await channel.declare_queue(REQUEST_QUEUE, durable=True)
            
            logger.info(f"Successfully connected to {REQUEST_QUEUE}, waiting for messages...")
            
            async with request_queue.iterator() as queue_iter:
                async for message in queue_iter:
                    async with message.process():
                        try:
                            # Parse the message content
                            content = json.loads(message.body.decode())
                            message_id = content.get("message_id")
                            file_path = content.get("file_path")
                            
                            logger.info(f"Received request to process file: {file_path}")
                            
                            if not file_path or not os.path.exists(file_path):
                                logger.error(f"File not found: {file_path}")
                                await send_result_to_queue({
                                    "message_id": message_id,
                                    "error": f"File not found: {file_path}",
                                    "status": "failed",
                                    "processed_at": datetime.now().isoformat()
                                })
                                continue
                            
                            # Process the audio file
                            await process_audio_file(file_path, message_id)
                            
                        except Exception as e:
                            logger.error(f"Error processing request message: {e}")
                            if message_id:
                                await send_result_to_queue({
                                    "message_id": message_id,
                                    "error": str(e),
                                    "status": "failed",
                                    "processed_at": datetime.now().isoformat()
                                })
        
        except asyncio.CancelledError:
            logger.info("Request queue listener cancelled")
            break
        except Exception as e:
            logger.error(f"Error in request queue listener: {e}")
            # Try to restart after a delay
            await asyncio.sleep(5)

@app.on_event("startup")
async def app_startup():
    """Start background tasks during application startup."""
    asyncio.create_task(start_response_queue_listener())
    logger.info("Response queue listener started")

    # Start the request queue listener as a background task
    asyncio.create_task(start_request_queue_listener())
    logger.info("Request queue listener started")

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
        
        # Get the channel lazily when needed
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

@app.get("/get-result/{message_id}")
async def get_result(message_id: str):
    """Get the processing result for a specific message ID."""
    if message_id in results_storage:
        return results_storage[message_id]["result"]
    else:
        return JSONResponse(
            status_code=404,
            content={
                "status": "not_found",
                "message": f"No result found for message ID: {message_id}"
            }
        )

@app.get("/list-results/")
async def list_results(limit: int = 10):
    """List the latest processing results."""
    # Sort results by timestamp (newest first) and limit the number
    sorted_results = sorted(
        results_storage.items(),
        key=lambda x: x[1]["timestamp"],
        reverse=True
    )[:limit]
    
    return {
        "count": len(sorted_results),
        "results": [
            {
                "message_id": key,
                "timestamp": value["timestamp"].isoformat(),
                "status": value["result"].get("status", "unknown"),
                "details": value["result"]
            }
            for key, value in sorted_results
        ]
    }

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
        "rabbitmq_url": RABBITMQ_URL,
        "results_count": len(results_storage)
    }


@app.post("/debug-flow/")
async def debug_flow(file_path: str):
    """Debug endpoint to test the entire flow in one request."""
    try:
        logger.info(f"DEBUG FLOW: Testing with file: {file_path}")
        
        # 1. Check if file exists
        if not os.path.exists(file_path):
            logger.error(f"DEBUG FLOW: File not found: {file_path}")
            return {"error": f"File not found: {file_path}"}
        
        # 2. Generate message ID
        message_id = str(uuid.uuid4())
        logger.info(f"DEBUG FLOW: Generated message ID: {message_id}")
        
        # 3. Process the file directly
        logger.info(f"DEBUG FLOW: Processing file directly")
        result = await process_audio_file(file_path, message_id)
        logger.info(f"DEBUG FLOW: Processing complete: {result}")
        
        # 4. Check if result was stored
        logger.info(f"DEBUG FLOW: Checking if result was stored")
        is_stored = message_id in results_storage
        if is_stored:
            stored_result = results_storage[message_id]["result"]
            logger.info(f"DEBUG FLOW: Result was stored: {stored_result}")
        else:
            logger.error(f"DEBUG FLOW: Result was NOT stored")
        
        # 5. Return debugging info
        return {
            "message_id": message_id,
            "file_processed": True,
            "result": result,
            "result_stored": is_stored,
            "storage_count": len(results_storage),
            "storage_keys": list(results_storage.keys())
        }
    
    except Exception as e:
        logger.error(f"DEBUG FLOW: Error: {e}")
        return {"error": str(e)}

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Motor Sound Detection API is running"}

if __name__ == "__main__":
    print("Starting server on port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)