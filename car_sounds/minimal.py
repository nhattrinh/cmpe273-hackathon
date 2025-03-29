"""
Lazy RabbitMQ initialization test app.
"""

import asyncio
import logging
import sys
import uvicorn
from fastapi import FastAPI
import aio_pika

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Initialize FastAPI application
app = FastAPI()

# RabbitMQ configuration
RABBITMQ_URL = "amqp://guest:guest@localhost:5672/"
REQUEST_QUEUE = "audio_processing_requests"
RESPONSE_QUEUE = "audio_processing_results"

@app.get("/")
async def root():
    logger.info("Root endpoint called")
    return {"message": "Hello World"}

@app.get("/test-rabbitmq")
async def test_rabbitmq():
    """Test RabbitMQ connection on demand."""
    try:
        logger.info(f"Attempting to connect to RabbitMQ at {RABBITMQ_URL}")
        
        # Try to establish connection
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        
        # Create channel
        channel = await connection.channel()
        
        # Declare queues
        await channel.declare_queue(REQUEST_QUEUE, durable=True)
        await channel.declare_queue(RESPONSE_QUEUE, durable=True)
        
        # Close connection
        await connection.close()
        
        return {
            "status": "success",
            "message": "Successfully connected to RabbitMQ and declared queues"
        }
    except Exception as e:
        logger.error(f"Failed to connect to RabbitMQ: {e}")
        return {
            "status": "error",
            "message": f"Failed to connect to RabbitMQ: {str(e)}"
        }

if __name__ == "__main__":
    print("Starting server with lazy RabbitMQ initialization on port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
