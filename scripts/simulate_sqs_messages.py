import boto3
import json
import uuid
import time
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

    # NUEVO: Crear la cola FIFO antes de usarla
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

    # 2. Crear datos dummy más completos para simular Novedades de MDM (Stibo STEP / Canales Digitales)
    # Incluimos datos que el motor de calidad (Fuzzy Matching con RapidFuzz) podría evaluar, 
    # como correos, teléfonos y direcciones.
    dummy_messages = [
        {
            "codigoCu": "CU-1001",
            "documentoNormalizado": "123456789",
            "nombre": "Juan Perez",
            "email": "jperez@ejemplo.com",
            "direccion": "Calle Falsa 123, Medellin",
            "telefono": "3001234567",
            "tipo_novedad": "creacion",
            "origen": "Celuweb"
        },
        {
            "codigoCu": "CU-1002",
            "documentoNormalizado": "987654321",
            "nombre": "Maria Gomez",
            "email": "mgomez@ejemplo.com",
            "direccion": "Carrera 45 # 10-20, Bogota",
            "telefono": "3109876543",
            "tipo_novedad": "modificacion",
            "origen": "Ecom"
        },
        {
            "codigoCu": "CU-1003",
            "documentoNormalizado": "1020304050",
            "nombre": "Carlos Restrepo S.A.S.",
            "email": "contacto@crestrepo.co",
            "direccion": "Av. Poblado 44-55, Medellin",
            "telefono": "3204445555",
            "tipo_novedad": "creacion",
            "origen": "Stibo STEP MDM"
        },
        {
            "codigoCu": "CU-1004",
            "documentoNormalizado": "1122334455",
            "nombre": "Distribuidora La Mayorista",
            "email": "gerencia@lamayorista.net",
            "direccion": "Cl 10 # 50-60, Cali",
            "telefono": "3015556677",
            "tipo_novedad": "rezonificacion",
            "origen": "Stibo STEP MDM"
        },
        {
            # Ejemplo para simular un candidato de Fuzzy Matching
            # Un cliente casi idéntico al CU-1001, que debería detectar el motor de calidad
            "codigoCu": "CU-1005",
            "documentoNormalizado": "123456780",
            "nombre": "Juan D Perez",
            "email": "jperez2@ejemplo.com",
            "direccion": "Calle Falsa 123",
            "telefono": "3001234567",
            "tipo_novedad": "creacion",
            "origen": "Celuweb"
        },
        {
            # ERROR 1: Campo esencial nulo (documentoNormalizado)
            "codigoCu": "CU-ERR-001",
            "documentoNormalizado": None,
            "nombre": "Cliente Sin Documento",
            "email": "sindoc@ejemplo.com",
            "direccion": "Desconocida",
            "telefono": "0000000000",
            "tipo_novedad": "creacion",
            "origen": "Celuweb"
        },
        {
            # ERROR 2: Estructura incompleta (Faltan múltiples campos)
            "codigoCu": "CU-ERR-002",
            "nombre": "Empresa Incompleta SAS",
            "tipo_novedad": "modificacion"
            # Falta origen, documentoNormalizado, etc.
        },
        {
            # ERROR 3: Tipos de datos inválidos (telefono como texto no numérico)
            "codigoCu": "CU-ERR-003",
            "documentoNormalizado": "ABCDEFGHIJ", # Debería ser numérico en la vida real
            "nombre": "Cliente Con Datos Invalidos",
            "email": "correo_invalido",
            "direccion": "Calle 123",
            "telefono": "NO_TIENE",
            "tipo_novedad": "novedad_desconocida", # Tipo no mapeado
            "origen": "OrigenDesconocido"
        }
    ]

    print(f"Enviando mensajes a la cola: {queue_url}")

    for data in dummy_messages:
        msg_body = json.dumps(data)
        
        # 3. Enviar el mensaje a la cola FIFO
        response = sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=msg_body,
            MessageGroupId=data['codigoCu'],
            MessageDeduplicationId=str(uuid.uuid4())
        )
        
        print(f"[*] Mensaje enviado exitosamente - codigoCu: {data['codigoCu']} | MessageId: {response.get('MessageId')}")
        time.sleep(1)

if __name__ == "__main__":
    main()