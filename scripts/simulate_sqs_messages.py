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

    # 2. Crear datos dummy con la NUEVA ESTRUCTURA para simular Novedades
    dummy_messages = [
        {
            # Caso 1: Persona Natural completa
            "primerNombre": "Juan",
            "segundoNombre": "Carlos",
            "primerApellido": "Pérez",
            "segundoApellido": "Gómez",
            "razonSocial": "",
            "documento": "1018273645",
            "direccion": "Calle 50 # 45-20",
            "direccion2": "Apto 301",
            "direccion3": "",
            "direccion4": "",
            "direccion5": "",
            "barrio": "El Poblado",
            "codigoCiudad": "5001",
            "codigoDepartamento": "5",
            "pais": "Colombia",
            "nombreNegocio": "Comercializadora J&P",
            "estadoCliente": "Activo",
            "peticionDeBorrado": "No",
            "peticionDeBorradoIndividual": "No",
            "situacionCliente": "Creado"
        },
        {
            # Caso 2: Persona Natural con variaciones (para evaluar Fuzzy Matching)
            "primerNombre": "Maria",
            "segundoNombre": "Fernanda",
            "primerApellido": "Lopez",
            "segundoApellido": "",
            "razonSocial": "",
            "documento": "987654321",
            "direccion": "Carrera 45 # 10-20",
            "direccion2": "Piso 2",
            "direccion3": "",
            "direccion4": "",
            "direccion5": "",
            "barrio": "Chapinero",
            "codigoCiudad": "11001",
            "codigoDepartamento": "11",
            "pais": "Colombia",
            "nombreNegocio": "Tienda Maria",
            "estadoCliente": "Activo",
            "peticionDeBorrado": "No",
            "peticionDeBorradoIndividual": "No",
            "situacionCliente": "Actualizado"
        },
        {
            # Caso 3: Persona Jurídica (con Razón Social)
            "primerNombre": "",
            "segundoNombre": "",
            "primerApellido": "",
            "segundoApellido": "",
            "razonSocial": "Distribuidora La Mayorista S.A.S.",
            "documento": "900123456",
            "direccion": "Cl 10 # 50-60",
            "direccion2": "Bodega 4",
            "direccion3": "",
            "direccion4": "",
            "direccion5": "",
            "barrio": "Zona Industrial",
            "codigoCiudad": "76001",
            "codigoDepartamento": "76",
            "pais": "Colombia",
            "nombreNegocio": "La Mayorista",
            "estadoCliente": "Activo",
            "peticionDeBorrado": "No",
            "peticionDeBorradoIndividual": "No",
            "situacionCliente": "Creado"
        },
        {
            # Caso 4: ERROR - Falta el documento (para probar validaciones del motor)
            "primerNombre": "Cliente",
            "segundoNombre": "Sin",
            "primerApellido": "Documento",
            "segundoApellido": "",
            "razonSocial": "",
            "documento": None,
            "direccion": "Desconocida",
            "direccion2": "",
            "direccion3": "",
            "direccion4": "",
            "direccion5": "",
            "barrio": "",
            "codigoCiudad": "0000",
            "codigoDepartamento": "0",
            "pais": "Colombia",
            "nombreNegocio": "Negocio Error",
            "estadoCliente": "Inactivo",
            "peticionDeBorrado": "Si",
            "peticionDeBorradoIndividual": "No",
            "situacionCliente": "Creado"
        }
    ]

    print(f"Enviando mensajes a la cola: {queue_url}")

    for data in dummy_messages:
        msg_body = json.dumps(data)
        
        # Obtenemos el documento para usarlo como MessageGroupId. 
        # Si viene nulo (caso de error), asignamos un ID por defecto para que SQS no falle.
        group_id = str(data.get('documento') or 'SIN_DOCUMENTO')
        
        # 3. Enviar el mensaje a la cola FIFO
        response = sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=msg_body,
            MessageGroupId=group_id,
            MessageDeduplicationId=str(uuid.uuid4())
        )
        
        print(f"[*] Mensaje enviado exitosamente - documento: {group_id} | MessageId: {response.get('MessageId')}")
        time.sleep(1)

if __name__ == "__main__":
    main()