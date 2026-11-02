"""Crea un usuario administrador desde la consola.

    python crear_admin.py
"""
import getpass
import sys

import database
from auth import hash_password
from database import ahora


def main():
    database.inicializar()
    nombre = input("Nombre completo: ").strip()
    email = input("Correo (usuario de ingreso): ").strip().lower()
    clave = getpass.getpass("Contraseña (mínimo 8 caracteres): ")

    if not nombre or not email or len(clave) < 8:
        sys.exit("Faltan datos o la contraseña es muy corta.")

    con = database.conectar()
    if con.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone():
        con.close()
        sys.exit(f"Ya existe un usuario con el correo {email}.")
    con.execute(
        "INSERT INTO usuarios (nombre, email, documento, cargo, password_hash, rol, activo,"
        " creado_en) VALUES (?, ?, '', '', ?, 'admin', 1, ?)",
        (nombre, email, hash_password(clave), ahora()))
    con.commit()
    con.close()
    print(f"Administrador {email} creado. Ya puedes ingresar en la plataforma.")


if __name__ == "__main__":
    main()
