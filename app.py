import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "some_secret_key"  # required for flash messages

DATABASE = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'tennis.db')

def get_db_connection():
    conn = sqlite3.connect(DATABASE, timeout=10)  # Wait up to 10 seconds for the lock to clear.
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")      # Use WAL mode to improve concurrent access.
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Create the squad table with two counters for roles
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS squad (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            ball_count INTEGER DEFAULT 0,
            court_count INTEGER DEFAULT 0
        )
    ''')
    # Create the appointments table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_date TEXT,
            appointment_time TEXT
        )
    ''')
    # Create the appointment_members table (many-to-many relation)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS appointment_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER,
            squad_id INTEGER,
            role TEXT DEFAULT 'none',
            FOREIGN KEY (appointment_id) REFERENCES appointments(id),
            FOREIGN KEY (squad_id) REFERENCES squad(id)
        )
    ''')
    conn.commit()
    conn.close()

def recalc_counts(squad_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    ball = 0
    court = 0
    records = cursor.execute("""
        SELECT am.role, a.appointment_date, a.appointment_time
        FROM appointment_members am
        JOIN appointments a ON am.appointment_id = a.id
        WHERE am.squad_id = ?
        ORDER BY a.appointment_date, a.appointment_time
    """, (squad_id,)).fetchall()
    for record in records:
        role = record['role']
        if role == 'none':
            ball += 1
            court += 1
        elif role == 'ball':
            ball = 0
            court += 1
        elif role == 'court':
            court = 0
            ball += 1
    conn.close()
    conn = get_db_connection()
    conn.execute("UPDATE squad SET ball_count = ?, court_count = ? WHERE id = ?", (ball, court, squad_id))
    conn.commit()
    conn.close()

@app.before_first_request
def initialize():
    init_db()

# --- Main Summary Page ---
@app.route('/')
def main():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS cnt FROM squad")
    squad_count = cursor.fetchone()['cnt']
    cursor.execute("SELECT COUNT(*) AS cnt FROM appointments")
    app_count = cursor.fetchone()['cnt']
    conn.close()
    return render_template('main.html', squad_count=squad_count, app_count=app_count)

# --- Squad Management ---
@app.route('/squad')
def squad():
    conn = get_db_connection()
    sort = request.args.get('sort')
    if sort == 'ball':
        query = "SELECT * FROM squad ORDER BY ball_count DESC"
    elif sort == 'court':
        query = "SELECT * FROM squad ORDER BY court_count DESC"
    else:
        query = "SELECT * FROM squad ORDER BY id"
    squad_list = conn.execute(query).fetchall()
    conn.close()
    return render_template('squad.html', squad=squad_list)

@app.route('/squad/add', methods=['GET', 'POST'])
def squad_add():
    if request.method == 'POST':
        name = request.form['name']
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO squad (name, ball_count, court_count) VALUES (?, 0, 0)",
                (name,)
            )
            conn.commit()
            flash('Squad member added successfully.', 'success')
        except sqlite3.IntegrityError:
            flash('Member already exists or invalid input.', 'danger')
        finally:
            conn.close()
        return redirect(url_for('squad'))
    return render_template('squad_add.html')

@app.route('/squad/edit/<int:id>', methods=['GET', 'POST'])
def squad_edit(id):
    conn = get_db_connection()
    member = conn.execute("SELECT * FROM squad WHERE id = ?", (id,)).fetchone()
    if not member:
        flash("Member not found.", "danger")
        return redirect(url_for('squad'))
    if request.method == 'POST':
        name = request.form['name']
        ball_count = request.form['ball_count']
        court_count = request.form['court_count']
        try:
            ball_count = int(ball_count)
            court_count = int(court_count)
        except ValueError:
            flash("Ball and Court counts must be integers.", "danger")
            return redirect(url_for('squad_edit', id=id))
        conn.execute("UPDATE squad SET name = ?, ball_count = ?, court_count = ? WHERE id = ?", (name, ball_count, court_count, id))
        conn.commit()
        conn.close()
        flash('Member updated successfully.', 'success')
        return redirect(url_for('squad'))
    conn.close()
    return render_template('squad_edit.html', member=member)

@app.route('/squad/delete/<int:id>', methods=['POST'])
def squad_delete(id):
    conn = get_db_connection()
    conn.execute("DELETE FROM squad WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    flash('Member deleted.', 'success')
    return redirect(url_for('squad'))

# --- Appointment Management ---
@app.route('/appointments')
def appointments():
    conn = get_db_connection()
    # Get list of appointments ordered by date and time
    appointments = conn.execute("SELECT * FROM appointments ORDER BY appointment_date, appointment_time").fetchall()
    # For each appointment, get the enrolled members and their role assignments
    appointment_data = []
    for appnt in appointments:
        members = conn.execute("""
            SELECT am.id AS am_id, s.id AS squad_id, s.name, am.role
            FROM appointment_members am
            JOIN squad s ON am.squad_id = s.id
            WHERE am.appointment_id = ?
        """, (appnt['id'],)).fetchall()
        appointment_data.append({'appointment': appnt, 'members': members})
    conn.close()
    return render_template('appointments.html', appointment_data=appointment_data)

@app.route('/appointment/new', methods=['GET', 'POST'])
def appointment_new():
    conn = get_db_connection()
    if request.method == 'POST':
        appointment_date = request.form['appointment_date']
        appointment_time = request.form['appointment_time']
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO appointments (appointment_date, appointment_time) VALUES (?, ?)",
            (appointment_date, appointment_time)
        )
        appointment_id = cursor.lastrowid
        selected_members = request.form.getlist('members')
        for member_id in selected_members:
            role = request.form.get(f'role_{member_id}', 'none')
            cursor.execute(
                "INSERT INTO appointment_members (appointment_id, squad_id, role) VALUES (?, ?, ?)",
                (appointment_id, member_id, role)
            )
            if role == 'none':
                cursor.execute(
                    "UPDATE squad SET ball_count = ball_count + 1, court_count = court_count + 1 WHERE id = ?",
                    (member_id,)
                )
            elif role == 'ball':
                cursor.execute(
                    "UPDATE squad SET ball_count = 0, court_count = court_count + 1 WHERE id = ?",
                    (member_id,)
                )
            elif role == 'court':
                cursor.execute(
                    "UPDATE squad SET court_count = 0, ball_count = ball_count + 1 WHERE id = ?",
                    (member_id,)
                )
        conn.commit()
        conn.close()
        flash('Appointment created successfully.', 'success')
        return redirect(url_for('appointments'))
    squad_list = conn.execute("SELECT * FROM squad").fetchall()
    conn.close()
    return render_template('appointment_new.html', squad=squad_list)

@app.route('/appointment/edit/<int:appointment_id>', methods=['GET', 'POST'])
def appointment_edit(appointment_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    appointment = cursor.execute(
        "SELECT * FROM appointments WHERE id = ?",
        (appointment_id,)
    ).fetchone()
    if not appointment:
        flash("Appointment not found.", "danger")
        conn.close()
        return redirect(url_for('appointments'))
    members = cursor.execute(
        """
        SELECT am.id AS am_id, am.squad_id, s.name, am.role
        FROM appointment_members am
        JOIN squad s ON am.squad_id = s.id
        WHERE am.appointment_id = ?
        """,
        (appointment_id,)
    ).fetchall()
    if request.method == 'POST':
        appointment_date = request.form['appointment_date']
        appointment_time = request.form['appointment_time']
        cursor.execute(
            "UPDATE appointments SET appointment_date = ?, appointment_time = ? WHERE id = ?",
            (appointment_date, appointment_time, appointment_id)
        )
        # Collect all member IDs whose role has changed
        changed_members = set()
        for m in members:
            member_id = m['squad_id']
            new_role = request.form.get(f'role_{member_id}', 'none')
            if new_role != m['role']:
                cursor.execute(
                    "UPDATE appointment_members SET role = ? WHERE id = ?",
                    (new_role, m['am_id'])
                )
                changed_members.add(member_id)
        conn.commit()
        conn.close()
        # After closing the main connection, update counts for each changed member
        for member_id in changed_members:
            recalc_counts(member_id)
        flash('Appointment updated.', 'success')
        return redirect(url_for('appointments'))
    conn.close()
    return render_template('appointment_edit.html', appointment=appointment, members=members)

if __name__ == '__main__':
    app.run(debug=True)