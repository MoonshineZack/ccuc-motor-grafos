import boto3
import json
import uuid
import time
import csv
import os
from botocore.exceptions import ClientError

def main():
    # 1. Configurar el cliente de boto3 apuntando a LocalStack
    sqs = boto3.client(
        'sqs',
        endpoint_url='http://localhost:4566',
        region_name='us-east-1',
        aws_access_key_id='test',
        aws_secret_access_key='test'
    )

    queue_name = 'ccuc-novedades-prod.fifo'
    queue_url = f'http://localhost:4566/000000000000/{queue_name}'

    # Crear/Verificar la cola FIFO antes de usarla
    print(f"Verificando/Creando la cola: {queue_name}...")
    try:
        sqs.create_queue(
            QueueName=queue_name,
            Attributes={
                'FifoQueue': 'true',
                'ContentBasedDeduplication': 'false'
            }
        )
        print("Cola lista.")
    except ClientError as e:
        print(f"Advertencia al crear la cola: {e}")

    # 2. Simular la lectura desde la Capa Media / S3 usando el archivo CSV local
    # Ajusta esta ruta si ejecutas el script desde otro directorio
    csv_file_path = os.path.join('Data', 'clientes_antioquia_sintetico.csv')
    
    # Alternativamente, si el archivo está en la misma carpeta donde corres el script:
    # csv_file_path = 'clientes_antioquia_sintetico.csv'

    print(f"Leyendo archivo CSV: {csv_file_path}")
    print(f"Enviando mensajes a la cola: {queue_url}")

    try:
        with open(csv_file_path, mode='r', encoding='utf-8') as csv_file:
            # DictReader mapea automáticamente las columnas a un diccionario usando la primera fila (headers)
            csv_reader = csv.DictReader(csv_file)
            
            for row in csv_reader:
                # Opcional: limpiar los espacios en blanco alrededor de las claves y valores
                data = {k.strip(): v.strip() for k, v in row.items() if k is not None}
                
                msg_body = json.dumps(data)
                
                # Obtenemos el documento para usarlo como MessageGroupId. 
                # En un CSV, los campos vacíos se leen como cadenas vacías (''), no como None.
                #doc = data.get('documento')
                doc = data.get('fiscalid')
                group_id = str(doc) if doc else 'SIN_DOCUMENTO'
                
                # 3. Enviar el mensaje a la cola FIFO
                response = sqs.send_message(
                    QueueUrl=queue_url,
                    MessageBody=msg_body,
                    MessageGroupId=group_id,
                    MessageDeduplicationId=str(uuid.uuid4())
                )
                
                print(f"[*] Mensaje enviado exitosamente - documento: {group_id} | MessageId: {response.get('MessageId')}")
                
                # Puedes reducir o eliminar el time.sleep si el archivo CSV es muy grande 
                # y quieres acelerar la ingesta en tu entorno local.
                time.sleep(0.1) 

    except FileNotFoundError:
        print(f"Error: No se encontró el archivo '{csv_file_path}'. Verifica que la ruta y el nombre sean correctos.")
    except Exception as e:
        print(f"Ocurrió un error inesperado al leer el CSV o enviar a SQS: {e}")

if __name__ == "__main__":
    main()