"""Tareas del worker por area (plan, seccion 2, regla 4). Cada modulo registra las suyas con shared_task y
worker/celery_app.py solo las importa y las agenda en beat_schedule. Las funciones de negocio reciben la sesion y la
fecha para poder probarlas sin Celery."""
