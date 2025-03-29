"""
Example client to demonstrate how to send audio files to the queue 
and receive processing results - modified for local execution.
"""

import asyncio
import aio_pika
import json
import uuid
import argparse
import os
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default configuration
RABBITMQ_URL = "amqp://guest:guest@localhost:5672/"
REQUEST_QUEUE = "audio_processing_requests"
RESPONSE_QUEUE = "audio_processing_results"

async def send_audio_file(file_path):
    """Send an audio file path to the processing queue."""
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return
    
    # Generate a unique message ID
    message_id = str(uuid.uuid4())
    
    # Create message payload
    message = {
        "message_id": message_id,
        "file_path": os.path.abspath(file_path)
    }
    
    # Connect to RabbitMQ and send message
    try:
        logger.info(f"Connecting to RabbitMQ at {RABBITMQ_URL}")
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        async with connection:
            logger.info("Connected to RabbitMQ")
            channel = await connection.channel()
            
            # Declare queues
            await channel.declare_queue(REQUEST_QUEUE, durable=True)
            await channel.declare_queue(RESPONSE_QUEUE, durable=True)
            
            # Send message to request queue
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(message).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                ),
                routing_key=REQUEST_QUEUE
            )
            
            logger.info(f"Sent file {file_path} to processing queue with message ID: {message_id}")
            
            # Set up consumer for response queue
            result_queue = await channel.declare_queue(RESPONSE_QUEUE, durable=True)
            
            logger.info(f"Waiting for processing results... (press Ctrl+C to exit)")
            
            async with result_queue.iterator() as queue_iter:
                async for message in queue_iter:
                    async with message.process():
                        result = json.loads(message.body.decode())
                        
                        # Check if this is the response for our request
                        if result.get("message_id") == message_id:
                            logger.info("Received result:")
                            if "error" in result:
                                logger.error(f"Processing failed: {result['error']}")
                            else:
                                logger.info(f"Predicted class: {result['predicted_class']}")
                                logger.info(f"Confidence: {result['confidence']}")
                                logger.info(f"All probabilities: {result['all_probabilities']}")
                            
                            # Exit the loop after receiving our result
                            return result
                        else:
                            # Skip messages for other requests
                            continue
    except Exception as e:
        logger.error(f"Error in client: {e}")
        raise

async def listen_for_results():
    """Just listen for all results coming from the response queue."""
    try:
        logger.info(f"Connecting to RabbitMQ at {RABBITMQ_URL}")
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        async with connection:
            logger.info("Connected to RabbitMQ")
            channel = await connection.channel()
            
            # Declare response queue
            result_queue = await channel.declare_queue(RESPONSE_QUEUE, durable=True)
            
            logger.info(f"Listening for all processing results... (press Ctrl+C to exit)")
            
            async with result_queue.iterator() as queue_iter:
                async for message in queue_iter:
                    async with message.process():
                        result = json.loads(message.body.decode())
                        logger.info(f"Received result for message ID: {result.get('message_id')}")
                        
                        if "error" in result:
                            logger.error(f"Processing failed: {result['error']}")
                        else:
                            logger.info(f"Predicted class: {result['predicted_class']}")
                            logger.info(f"Confidence: {result['confidence']}")
    except Exception as e:
        logger.error(f"Error in client: {e}")
        raise

def main():
    parser = argparse.ArgumentParser(description="Audio processing client")
    parser.add_argument("--file", help="Path to audio file to process")
    parser.add_argument("--listen", action="store_true", help="Just listen for results")
    
    args = parser.parse_args()
    
    try:
        if args.listen:
            asyncio.run(listen_for_results())
        elif args.file:
            asyncio.run(send_audio_file(args.file))
        else:
            parser.print_help()
    except KeyboardInterrupt:
        logger.info("Client stopped by user")
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    main()
