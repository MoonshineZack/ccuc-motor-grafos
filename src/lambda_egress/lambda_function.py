import json
import boto3
import os
import datetime

# Inicializar cliente S3. Usamos variables de entorno para soportar LocalStack
s3_client = boto3.client(
    's3',
    endpoint_url=os.getenv('ENDPOINT_URL', 'http://localhost:4566'),
    region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID', 'test'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY', 'test')
)

TARGET_BUCKET = os.getenv('DATALAKE_BUCKET', 'ccuc-landing')

def lambda_handler(event, context):
    """
    Simula la AWS Lambda (Egress Orchestrator)
    Recibe el Payload Triada desde el Motor de Reglas (ECS)
    y lo escribe en el Data Lake (Amazon S3).
    """
    try:
        print("Lambda Egress Orchestrator invocada con evento:", event)
        
        # 1. Extraer el payload Triada.
        # Asumiendo que el Motor de Reglas lo envía en el body si es API, o directo en el event si es invocación directa.
        payload = event.get('body')
        if not payload:
            payload = event  # Invocación directa
            
        if isinstance(payload, str):
            payload = json.loads(payload)
            
        codigo_cu = payload.get('codigoCu', 'UNKNOWN_CU')
        
        # 2. Preparar el objeto a guardar en S3
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        s3_key = f"triadas-resueltas/{codigo_cu}/{timestamp}_payload.json"
        
        # 3. Escribir en Amazon S3 (Data Lake)
        s3_client.put_object(
            Bucket=TARGET_BUCKET,
            Key=s3_key,
            Body=json.dumps(payload, indent=2),
            ContentType='application/json'
        )
        
        print(f"Triada escrita exitosamente en s3://{TARGET_BUCKET}/{s3_key}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Triada guardada exitosamente', 's3_path': s3_key})
        }
        
    except Exception as e:
        print(f"Error procesando la triada en Lambda: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

# Código auxiliar para probar la Lambda de manera local
if __name__ == "__main__":
    # Evento simulado como si viniera del ECS
    test_event = {
        "codigoCu": "CU-1001",
        "golden_record_id": "GR-9999",
        "matches_found": True,
        "score_calidad": 98.5,
        "datos_consolidados": {
            "nombre": "Juan Perez",
            "documentoNormalizado": "123456789"
        }
    }
    
    # Asegurar que el bucket exista localmente para la prueba
    try:
        s3_client.create_bucket(Bucket=TARGET_BUCKET)
    except Exception as e:
        pass # Podría ya existir
        
    print("Probando Lambda localmente...")
    resultado = lambda_handler(test_event, None)
    print("Resultado:", resultado)
