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
    csv_file_path = os.path.join('Data', 'clientes_antioquia_sintetico.csv')

    print(f"Leyendo archivo CSV: {csv_file_path}")
    print(f"Enviando mensajes a la cola: {queue_url}")

    try:
        with open(csv_file_path, mode='r', encoding='utf-8') as csv_file:
            csv_reader = csv.DictReader(csv_file)

            for row in csv_reader:
                data = {k.strip(): v.strip() for k, v in row.items() if k is not None}

                # Mapeo de la estructura CSV a la estructura JSON esperada
                # Se asume que nombres compuestos vienen en 'agentname' y se envían a primerNombre por simplicidad
                payload = {
                    "CodigoTransaccional": data.get('step_id_cliente_unico', ''),
                    "primerNombre": data.get('agentname', ''),
                    "segundoNombre": "",
                    "primerApellido": "",
                    "segundoApellido": "",
                    "razonSocial": data.get('companyname', ''),
                    "documento": data.get('fiscalid', ''),
                    "direccion": data.get('address', ''),
                    "direccion2": "",
                    "direccion3": "",
                    "direccion4": "",
                    "direccion5": "",
                    "barrio": data.get('neighborhood', ''),
                    "codigoCiudad": data.get('municipality', ''),
                    "codigoDepartamento": data.get('department', ''),
                    "pais": "Colombia", # O data.get('country', 'Colombia')
                    "nombreNegocio": data.get('establishmentname', ''),
                    "estadoCliente": data.get('statuscodedesc', 'Activo'),
                    "peticionDeBorrado": "No",
                    "peticionDeBorradoIndividual": "No",
                    "situacionCliente": "Creado"
                }

                msg_body = json.dumps(payload)

                # Usamos el documento para el MessageGroupId
                doc = payload.get('documento')
                group_id = str(doc) if doc else 'SIN_DOCUMENTO'

                # 3. Enviar el mensaje a la cola FIFO
                response = sqs.send_message(
                    QueueUrl=queue_url,
                    MessageBody=msg_body,
                    MessageGroupId=group_id,
                    MessageDeduplicationId=str(uuid.uuid4())
                )

                print(f"[*] Mensaje enviado exitosamente - documento: {group_id} | MessageId: {response.get('MessageId')}")

                time.sleep(0.1)

    except FileNotFoundError:
        print(f"Error: No se encontró el archivo '{csv_file_path}'. Verifica que la ruta y el nombre sean correctos.")
    except Exception as e:
        print(f"Ocurrió un error inesperado al leer el CSV o enviar a SQS: {e}")

if __name__ == "__main__":
    main()