"""
UPI Auto-Payment Verifier API - ULTRA FAST VERSION
Instant verification with 0.5-second response time
QR: https://upi-qrcode-generater-wroy.vercel.app/qr/9304619487@fam/{amount}/KHAN STORE
"""

import os
import re
import time
import json
import logging
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# ============================================
# CONFIGURATION
# ============================================
CONFIG = {
    'UPI_ID': '9304619487@fam',
    'PAYEE_NAME': 'KHAN STORE',
    'GMAIL_APP_PASSWORD': 'owjwtlotkfjnsftm',
    'GMAIL_EMAIL': 'nkg166465@gmail.com',
    'POLL_INTERVAL': 0.5,  # ⚡ ULTRA FAST - 0.5 seconds!
    'POLL_TIMEOUT': 60,
    'QR_BASE_URL': 'https://upi-qrcode-generater-wroy.vercel.app/qr',
    'PORT': int(os.getenv('PORT', 5000)),
    'TIME_WINDOW_MINUTES': 5
}

# ============================================
# LOGGING
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# FLASK APP
# ============================================
app = Flask(__name__)
CORS(app)

# ============================================
# IMAP FUNCTIONS
# ============================================

def connect_imap(email_address: str = None, password: str = None):
    """Connect to Gmail using IMAP with App Password"""
    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(
            email_address or CONFIG['GMAIL_EMAIL'],
            password or CONFIG['GMAIL_APP_PASSWORD']
        )
        mail.select('INBOX')
        logger.info(f"✅ IMAP connected successfully")
        return mail
    except Exception as e:
        logger.error(f"IMAP connection error: {e}")
        raise Exception(f"Failed to connect to Gmail: {str(e)}")

def get_email_body_from_imap(mail, msg_id: str) -> str:
    """Get full email body from message ID using IMAP"""
    try:
        result, data = mail.fetch(msg_id, '(RFC822)')
        if result != 'OK':
            return ''
        
        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)
        
        body = ''
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition'))
                
                if content_type == 'text/plain' and 'attachment' not in content_disposition:
                    try:
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break
                    except:
                        continue
        else:
            try:
                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            except:
                body = ''
        
        if not body:
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    if content_type == 'text/html':
                        try:
                            html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                            body = re.sub(r'<[^>]+>', ' ', html)
                            body = re.sub(r'\s+', ' ', body).strip()
                            break
                        except:
                            continue
        
        return body
    except Exception as e:
        logger.error(f"Error getting email body: {e}")
        return ''

def parse_payment_email(body: str) -> Dict[str, Any]:
    """Parse email body to extract payment details"""
    details = {
        'amount': None,
        'transaction_id': None,
        'utr': None,
        'date': None,
        'balance': None,
        'sender': None,
        'purpose': None,
        'type': None,
        'raw_preview': body[:200],
        'payment_datetime': None,
        'time_diff_minutes': None
    }
    
    if 'successfully received' in body.lower():
        details['type'] = 'received'
        logger.info("📥 Transaction type: RECEIVED")
    elif 'successfully paid' in body.lower():
        details['type'] = 'paid'
        logger.info("📤 Transaction type: PAID - SKIPPING")
        return details
    
    amount_patterns = [
        r'₹([0-9]+(\.[0-9]+)?)',
        r'Amount\s*[:]\s*₹([0-9]+(\.[0-9]+)?)',
        r'Rs\.?\s*([0-9]+(\.[0-9]+)?)',
        r'INR\s*([0-9]+(\.[0-9]+)?)',
        r'([0-9]+(\.[0-9]+)?)\s*INR',
        r'([0-9]+(\.[0-9]+)?)\s*Rs\.?',
    ]
    
    for pattern in amount_patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            details['amount'] = float(match.group(1))
            logger.info(f"💰 Found amount: ₹{details['amount']}")
            break
    
    tx_patterns = [
        r'Transaction ID\s*[:]\s*([A-Z0-9]+)',
        r'Txn ID\s*[:]\s*([A-Z0-9]+)',
        r'Transaction\s*ID\s*[:]\s*([A-Z0-9]+)',
        r'Txn\s*[:]\s*([A-Z0-9]+)',
        r'with transaction id\s*([A-Z0-9]+)',
    ]
    for pattern in tx_patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            details['transaction_id'] = match.group(1)
            logger.info(f"📋 Transaction ID: {details['transaction_id']}")
            break
    
    utr_match = re.search(r'UTR\s*[:]\s*([0-9]+)', body, re.IGNORECASE)
    if utr_match:
        details['utr'] = utr_match.group(1)
    
    date_match = re.search(
        r'([0-9]{2}:[0-9]{2}\s*(AM|PM)\s*IST,\s*[0-9]{2}\s*[A-Za-z]+\s*[0-9]{4})',
        body, re.IGNORECASE
    )
    if date_match:
        details['date'] = date_match.group(1)
        
        try:
            time_str = date_match.group(1)
            time_part = re.search(r'([0-9]{2}:[0-9]{2})\s*(AM|PM)', time_str)
            if time_part:
                hour, minute = map(int, time_part.group(1).split(':'))
                ampm = time_part.group(2)
                
                if ampm == 'PM' and hour != 12:
                    hour += 12
                elif ampm == 'AM' and hour == 12:
                    hour = 0
                
                now = datetime.now()
                payment_time = datetime(now.year, now.month, now.day, hour, minute)
                
                if payment_time > now:
                    payment_time = payment_time - timedelta(days=1)
                
                details['payment_datetime'] = payment_time
                details['time_diff_minutes'] = round((now - payment_time).total_seconds() / 60, 1)
                
                logger.info(f"⏰ Payment at: {payment_time.strftime('%H:%M')}, {details['time_diff_minutes']} minutes ago")
        except Exception as e:
            logger.warning(f"Could not parse date: {e}")
    
    balance_match = re.search(r'Updated Balance\s*[:]\s*₹([0-9]+(\.[0-9]+)?)', body, re.IGNORECASE)
    if balance_match:
        details['balance'] = float(balance_match.group(1))
    
    sender_match = re.search(r'from\s*([A-Za-z\s.]+)', body, re.IGNORECASE)
    if sender_match:
        details['sender'] = sender_match.group(1).strip()
    
    purpose_match = re.search(r'Purpose\s*[:]\s*(.+)', body, re.IGNORECASE)
    if purpose_match:
        details['purpose'] = purpose_match.group(1).strip()
    
    return details

def search_payment_email_imap(mail, amount: float, start_timestamp: int, check_count: int = 0, time_window_minutes: int = 5) -> Optional[Dict[str, Any]]:
    """ULTRA FAST - Search ONLY the most recent emails"""
    try:
        logger.info(f"⚡ ULTRA FAST Search (Attempt {check_count}) - Last {time_window_minutes} minutes")
        
        result, data = mail.search(None, 'ALL')
        if result != 'OK':
            return None
        
        email_ids = data[0].split()
        if not email_ids:
            logger.info(f"❌ No emails found")
            return None
        
        # ⚡ Check ONLY the most recent 10 emails for speed
        for msg_id in email_ids[-10:]:
            msg_id_str = msg_id.decode('utf-8') if isinstance(msg_id, bytes) else str(msg_id)
            
            try:
                body = get_email_body_from_imap(mail, msg_id_str)
                
                if not body:
                    continue
                
                payment_details = parse_payment_email(body)
                
                if payment_details.get('type') != 'received':
                    continue
                
                payment_datetime = payment_details.get('payment_datetime')
                if payment_datetime:
                    time_diff = payment_details.get('time_diff_minutes', 0)
                    
                    if time_diff > time_window_minutes:
                        continue
                    else:
                        logger.info(f"✅ Payment is {time_diff} minutes old - Within window")
                else:
                    result, data = mail.fetch(msg_id, '(BODY.PEEK[HEADER.FIELDS (DATE)])')
                    if result == 'OK':
                        header_data = data[0][1].decode('utf-8', errors='ignore')
                        date_match = re.search(r'Date:\s*(.+)', header_data, re.IGNORECASE)
                        if date_match:
                            try:
                                email_date = email.utils.parsedate_to_datetime(date_match.group(1))
                                time_diff = (datetime.now(email_date.tzinfo) - email_date).total_seconds() / 60 if email_date.tzinfo else (datetime.now() - email_date).total_seconds() / 60
                                if time_diff > time_window_minutes:
                                    continue
                            except:
                                pass
                
                found_amount = payment_details.get('amount')
                
                if found_amount:
                    logger.info(f"💰 Found: ₹{found_amount}, Expected: ₹{amount}")
                    
                    if abs(found_amount - float(amount)) < 0.01:
                        logger.info(f"✅✅✅ MATCH FOUND! Received ₹{found_amount}")
                        payment_details['email_id'] = msg_id_str
                        payment_details['timestamp'] = datetime.now().isoformat()
                        payment_details['check_count'] = check_count
                        return payment_details
                    else:
                        logger.info(f"❌ Amount mismatch: found ₹{found_amount}, expected ₹{amount}")
                
            except Exception as e:
                logger.warning(f"Error processing email {msg_id_str}: {e}")
                continue
        
        return None
        
    except Exception as e:
        logger.error(f"Error searching email: {e}")
        return None

# ============================================
# API ENDPOINTS
# ============================================

@app.route('/generate-qr', methods=['GET'])
def generate_qr():
    """Generate QR for KHAN STORE"""
    amount = request.args.get('amount')
    
    if not amount:
        return jsonify({
            'status': 'error',
            'message': 'Amount is required. Example: ?amount=99'
        }), 400
    
    try:
        num_amount = float(amount)
        if num_amount <= 0:
            raise ValueError("Amount must be positive")
    except ValueError:
        return jsonify({
            'status': 'error',
            'message': 'Amount must be a positive number'
        }), 400
    
    # ✅ KHAN STORE QR URL
    qr_url = f"https://upi-qrcode-generater-wroy.vercel.app/qr/9304619487@fam/{num_amount}/KHAN%20STORE"
    
    return jsonify({
        'status': 'success',
        'qr_url': qr_url,
        'amount': num_amount,
        'upi_id': '9304619487@fam',
        'payee': 'KHAN STORE',
        'instructions': 'Scan this QR code using any UPI app to pay'
    })

@app.route('/verify-payment', methods=['POST', 'GET'])
def verify_payment():
    """ULTRA FAST verification - 0.5 second polling"""
    if request.method == 'GET':
        amount = request.args.get('amount')
        session_id = request.args.get('session_id')
        time_window = request.args.get('time_window', CONFIG['TIME_WINDOW_MINUTES'])
    else:
        data = request.get_json()
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'Invalid request body'
            }), 400
        amount = data.get('amount')
        session_id = data.get('session_id')
        time_window = data.get('time_window', CONFIG['TIME_WINDOW_MINUTES'])
    
    if not session_id:
        session_id = f'session_{int(time.time())}_{os.urandom(4).hex()}'
    
    logger.info(f"[{session_id}] ⚡ ULTRA FAST verification for ₹{amount}")
    
    if amount is None:
        return jsonify({
            'status': 'error',
            'message': 'Amount is required'
        }), 400
    
    try:
        num_amount = float(amount)
        if num_amount <= 0:
            raise ValueError("Amount must be positive")
        time_window = int(time_window)
    except ValueError:
        return jsonify({
            'status': 'error',
            'message': 'Amount must be a positive number and time_window must be integer'
        }), 400
    
    try:
        mail = connect_imap()
        start_timestamp = int(time.time())
        
        qr_url = f"https://upi-qrcode-generater-wroy.vercel.app/qr/9304619487@fam/{num_amount}/KHAN%20STORE"
        
        max_attempts = CONFIG['POLL_TIMEOUT'] // CONFIG['POLL_INTERVAL']
        
        for attempt in range(1, max_attempts + 1):
            logger.info(f"[{session_id}] ⚡ Checking attempt {attempt}/{max_attempts}")
            
            result = search_payment_email_imap(mail, num_amount, start_timestamp, attempt, time_window)
            
            if result and result.get('amount'):
                if abs(result.get('amount') - num_amount) < 0.01 and result.get('type') == 'received':
                    result['status'] = 'success'
                    result['message'] = f'✅ Payment verified! (Received {result.get("time_diff_minutes", 0)} minutes ago)'
                    result['qr_url'] = qr_url
                    result['session_id'] = session_id
                    result['attempt'] = attempt
                    result['time_window_minutes'] = time_window
                    result['response_time'] = f'{attempt * 0.5} seconds'
                    mail.close()
                    mail.logout()
                    return jsonify(result)
            
            time.sleep(CONFIG['POLL_INTERVAL'])
        
        mail.close()
        mail.logout()
        
        return jsonify({
            'status': 'pending',
            'amount': num_amount,
            'qr_url': qr_url,
            'session_id': session_id,
            'time_window_minutes': time_window,
            'message': f'⏰ No RECEIVED payment of ₹{num_amount} found in last {time_window} minutes.'
        })
        
    except Exception as e:
        logger.error(f"[{session_id}] Error: {e}")
        return jsonify({
            'status': 'error',
            'message': f'❌ Payment verification failed: {str(e)}',
            'session_id': session_id
        }), 500

@app.route('/verify-realtime', methods=['GET'])
def verify_realtime():
    """ULTRA FAST Realtime verification - 0.5 second polling"""
    amount = request.args.get('amount')
    time_window = request.args.get('time_window', CONFIG['TIME_WINDOW_MINUTES'])
    
    if not amount:
        return jsonify({
            'status': 'error',
            'message': 'Amount is required'
        }), 400
    
    try:
        num_amount = float(amount)
        if num_amount <= 0:
            raise ValueError("Amount must be positive")
        time_window = int(time_window)
    except ValueError:
        return jsonify({
            'status': 'error',
            'message': 'Amount must be a positive number and time_window must be integer'
        }), 400
    
    def generate():
        session_id = f'realtime_{int(time.time())}_{os.urandom(4).hex()}'
        start_timestamp = int(time.time())
        attempt = 0
        max_attempts = 120  # 60 seconds / 0.5 seconds
        
        try:
            mail = connect_imap()
            
            yield f"data: {json.dumps({'status': 'checking', 'message': f'⚡ ULTRA FAST - Searching for ₹{num_amount} in last {time_window} minutes...', 'amount': num_amount, 'session_id': session_id, 'time_window': time_window})}\n\n"
            
            while attempt < max_attempts:
                attempt += 1
                
                result = search_payment_email_imap(mail, num_amount, start_timestamp, attempt, time_window)
                
                if result and result.get('amount') and result.get('type') == 'received':
                    if abs(result.get('amount') - num_amount) < 0.01:
                        result['status'] = 'success'
                        result['message'] = f'✅ Payment verified! (Received {result.get("time_diff_minutes", 0)} minutes ago)'
                        result['session_id'] = session_id
                        result['attempt'] = attempt
                        result['time_window_minutes'] = time_window
                        result['response_time'] = f'{attempt * 0.5} seconds'
                        yield f"data: {json.dumps(result)}\n\n"
                        mail.close()
                        mail.logout()
                        break
                
                progress = {
                    'status': 'waiting',
                    'message': f'⏳ Waiting for payment... Attempt {attempt}/{max_attempts} (0.5s interval)',
                    'amount': num_amount,
                    'session_id': session_id,
                    'attempt': attempt,
                    'max_attempts': max_attempts,
                    'time_window': time_window,
                    'progress': round((attempt / max_attempts) * 100, 1)
                }
                yield f"data: {json.dumps(progress)}\n\n"
                time.sleep(CONFIG['POLL_INTERVAL'])
            
            if attempt >= max_attempts:
                timeout_msg = {
                    'status': 'timeout',
                    'message': f'⏰ No payment of ₹{num_amount} found in last {time_window} minutes.',
                    'amount': num_amount,
                    'session_id': session_id,
                    'time_window': time_window
                }
                yield f"data: {json.dumps(timeout_msg)}\n\n"
                mail.close()
                mail.logout()
                
        except Exception as e:
            error_msg = {
                'status': 'error',
                'message': f'❌ Error: {str(e)}',
                'session_id': session_id
            }
            yield f"data: {json.dumps(error_msg)}\n\n"
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )

@app.route('/verify-last-payment', methods=['GET'])
def verify_last_payment():
    """⚡ FASTEST - One-time check (0.5 seconds)"""
    amount = request.args.get('amount')
    time_window = request.args.get('time_window', CONFIG['TIME_WINDOW_MINUTES'])
    
    if not amount:
        return jsonify({
            'status': 'error',
            'message': 'Amount is required. Example: ?amount=1&time_window=5'
        }), 400
    
    try:
        num_amount = float(amount)
        time_window = int(time_window)
    except ValueError:
        return jsonify({
            'status': 'error',
            'message': 'Amount must be a number and time_window must be integer'
        }), 400
    
    try:
        mail = connect_imap()
        
        start_time = time.time()
        result = search_payment_email_imap(mail, num_amount, int(time.time()), 1, time_window)
        end_time = time.time()
        
        mail.close()
        mail.logout()
        
        if result and result.get('amount') and result.get('type') == 'received':
            if abs(result.get('amount') - num_amount) < 0.01:
                result['status'] = 'success'
                result['message'] = f'✅ Payment verified! (Received {result.get("time_diff_minutes", 0)} minutes ago)'
                result['time_window_minutes'] = time_window
                result['response_time'] = f'{round((end_time - start_time) * 1000)}ms'
                return jsonify(result)
        
        return jsonify({
            'status': 'not_found',
            'message': f'❌ No RECEIVED payment of ₹{num_amount} found in last {time_window} minutes',
            'amount': num_amount,
            'time_window': time_window,
            'response_time': f'{round((end_time - start_time) * 1000)}ms'
        })
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error: {str(e)}'
        }), 500

@app.route('/debug-emails', methods=['GET'])
def debug_emails():
    """Debug endpoint - Show recent emails"""
    try:
        mail = connect_imap()
        
        result, data = mail.search(None, 'ALL')
        if result != 'OK':
            return jsonify({
                'status': 'error',
                'message': 'Failed to search emails'
            }), 500
        
        email_ids = data[0].split()
        if not email_ids:
            return jsonify({
                'status': 'success',
                'gmail': CONFIG['GMAIL_EMAIL'],
                'total_emails': 0,
                'emails': []
            })
        
        emails = []
        now = datetime.now()
        
        for msg_id in email_ids[-20:]:
            msg_id_str = msg_id.decode('utf-8') if isinstance(msg_id, bytes) else str(msg_id)
            
            try:
                body = get_email_body_from_imap(mail, msg_id_str)
                details = parse_payment_email(body)
                
                within_5_min = False
                time_ago = None
                payment_time = details.get('payment_datetime')
                if payment_time:
                    time_ago = (now - payment_time).total_seconds() / 60
                    within_5_min = time_ago <= 5
                
                emails.append({
                    'id': msg_id_str,
                    'amount_found': details.get('amount'),
                    'transaction_type': details.get('type'),
                    'transaction_id': details.get('transaction_id'),
                    'sender': details.get('sender'),
                    'payment_time': details.get('date'),
                    'minutes_ago': round(time_ago, 1) if time_ago is not None else None,
                    'within_5_minutes': within_5_min,
                    'is_received': details.get('type') == 'received',
                    'body_preview': body[:150] if body else 'No body'
                })
            except Exception as e:
                emails.append({
                    'id': msg_id_str,
                    'error': str(e)
                })
        
        mail.close()
        mail.logout()
        
        return jsonify({
            'status': 'success',
            'gmail': CONFIG['GMAIL_EMAIL'],
            'total_emails': len(emails),
            'current_time': now.isoformat(),
            'time_window_minutes': CONFIG['TIME_WINDOW_MINUTES'],
            'emails': emails
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/change-credentials', methods=['POST'])
def change_credentials():
    data = request.get_json() if request.is_json else request.args.to_dict()
    
    if not data:
        return jsonify({
            'status': 'error',
            'message': 'Invalid request. Provide email and/or password in query string or JSON body.'
        }), 400
    
    new_email = data.get('email')
    new_password = data.get('password')
    
    if not new_email and not new_password:
        return jsonify({
            'status': 'error',
            'message': 'At least one of email or password is required'
        }), 400
    
    if new_password and len(new_password) != 16:
        return jsonify({
            'status': 'error',
            'message': 'Password must be exactly 16 characters'
        }), 400
    
    if new_email and not re.match(r'^[a-zA-Z0-9._%+-]+@gmail\.com$', new_email):
        return jsonify({
            'status': 'error',
            'message': 'Email must be a valid Gmail address (ending with @gmail.com)'
        }), 400
    
    test_email = new_email or CONFIG['GMAIL_EMAIL']
    test_password = new_password or CONFIG['GMAIL_APP_PASSWORD']
    
    try:
        test_mail = imaplib.IMAP4_SSL('imap.gmail.com')
        test_mail.login(test_email, test_password)
        test_mail.logout()
        logger.info(f"✅ Credentials test successful for {test_email}")
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Invalid credentials: {str(e)}'
        }), 400
    
    old_email = CONFIG['GMAIL_EMAIL']
    old_password = CONFIG['GMAIL_APP_PASSWORD']
    
    if new_email:
        CONFIG['GMAIL_EMAIL'] = new_email
    if new_password:
        CONFIG['GMAIL_APP_PASSWORD'] = new_password
    
    try:
        with open('.env', 'r') as f:
            lines = f.readlines()
        
        with open('.env', 'w') as f:
            for line in lines:
                if line.startswith('GMAIL_EMAIL=') and new_email:
                    f.write(f'GMAIL_EMAIL={new_email}\n')
                elif line.startswith('GMAIL_APP_PASSWORD=') and new_password:
                    f.write(f'GMAIL_APP_PASSWORD={new_password}\n')
                else:
                    f.write(line)
        
        logger.info("✅ Credentials updated in .env file")
    except Exception as e:
        logger.error(f"Error updating .env: {e}")
        CONFIG['GMAIL_EMAIL'] = old_email
        CONFIG['GMAIL_APP_PASSWORD'] = old_password
        return jsonify({
            'status': 'error',
            'message': f'Failed to update .env file: {str(e)}'
        }), 500
    
    return jsonify({
        'status': 'success',
        'message': '✅ Credentials updated successfully',
        'email': CONFIG['GMAIL_EMAIL'],
        'password_length': len(CONFIG['GMAIL_APP_PASSWORD']) if CONFIG['GMAIL_APP_PASSWORD'] else 0,
        'changes': {
            'email_changed': bool(new_email),
            'password_changed': bool(new_password)
        }
    })

@app.route('/change-password', methods=['POST'])
def change_password():
    data = request.get_json() if request.is_json else request.args.to_dict()
    
    if not data:
        return jsonify({
            'status': 'error',
            'message': 'Invalid request body'
        }), 400
    
    new_password = data.get('password')
    if not new_password:
        return jsonify({
            'status': 'error',
            'message': 'Password is required'
        }), 400
    
    if len(new_password) != 16:
        return jsonify({
            'status': 'error',
            'message': 'Password must be exactly 16 characters'
        }), 400
    
    try:
        test_mail = imaplib.IMAP4_SSL('imap.gmail.com')
        test_mail.login(CONFIG['GMAIL_EMAIL'], new_password)
        test_mail.logout()
        logger.info("✅ New password test successful")
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Invalid password: {str(e)}'
        }), 400
    
    old_password = CONFIG['GMAIL_APP_PASSWORD']
    CONFIG['GMAIL_APP_PASSWORD'] = new_password
    
    try:
        with open('.env', 'r') as f:
            lines = f.readlines()
        
        with open('.env', 'w') as f:
            for line in lines:
                if line.startswith('GMAIL_APP_PASSWORD='):
                    f.write(f'GMAIL_APP_PASSWORD={new_password}\n')
                else:
                    f.write(line)
        
        logger.info("✅ Password updated in .env file")
    except Exception as e:
        logger.error(f"Error updating .env: {e}")
        CONFIG['GMAIL_APP_PASSWORD'] = old_password
        return jsonify({
            'status': 'error',
            'message': f'Failed to update .env file: {str(e)}'
        }), 500
    
    return jsonify({
        'status': 'success',
        'message': '✅ Password updated successfully',
        'email': CONFIG['GMAIL_EMAIL'],
        'password_length': len(new_password)
    })

@app.route('/change-email', methods=['POST'])
def change_email():
    data = request.get_json() if request.is_json else request.args.to_dict()
    
    if not data:
        return jsonify({
            'status': 'error',
            'message': 'Invalid request body'
        }), 400
    
    new_email = data.get('email')
    if not new_email:
        return jsonify({
            'status': 'error',
            'message': 'Email is required'
        }), 400
    
    if not re.match(r'^[a-zA-Z0-9._%+-]+@gmail\.com$', new_email):
        return jsonify({
            'status': 'error',
            'message': 'Email must be a valid Gmail address (ending with @gmail.com)'
        }), 400
    
    try:
        test_mail = imaplib.IMAP4_SSL('imap.gmail.com')
        test_mail.login(new_email, CONFIG['GMAIL_APP_PASSWORD'])
        test_mail.logout()
        logger.info(f"✅ New email test successful: {new_email}")
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Invalid credentials: {str(e)}'
        }), 400
    
    old_email = CONFIG['GMAIL_EMAIL']
    CONFIG['GMAIL_EMAIL'] = new_email
    
    try:
        with open('.env', 'r') as f:
            lines = f.readlines()
        
        with open('.env', 'w') as f:
            for line in lines:
                if line.startswith('GMAIL_EMAIL='):
                    f.write(f'GMAIL_EMAIL={new_email}\n')
                else:
                    f.write(line)
        
        logger.info(f"✅ Email updated in .env file: {new_email}")
    except Exception as e:
        logger.error(f"Error updating .env: {e}")
        CONFIG['GMAIL_EMAIL'] = old_email
        return jsonify({
            'status': 'error',
            'message': f'Failed to update .env file: {str(e)}'
        }), 500
    
    return jsonify({
        'status': 'success',
        'message': '✅ Email updated successfully',
        'email': CONFIG['GMAIL_EMAIL']
    })

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'gmail': CONFIG['GMAIL_EMAIL'],
        'upi_id': CONFIG['UPI_ID'],
        'payee': CONFIG['PAYEE_NAME'],
        'auth_method': 'IMAP with App Password',
        'time_window_minutes': CONFIG['TIME_WINDOW_MINUTES'],
        'poll_interval': f"{CONFIG['POLL_INTERVAL']} seconds",
        'version': '3.0.0-ULTRA-FAST'
    })

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'name': 'UPI Auto-Payment Verifier API',
        'version': '3.0.0-ULTRA-FAST',
        'gmail': CONFIG['GMAIL_EMAIL'],
        'upi_id': CONFIG['UPI_ID'],
        'payee': CONFIG['PAYEE_NAME'],
        'status': '⚡ ULTRA FAST - 0.5 SECOND POLLING',
        'qr_url_format': 'https://upi-qrcode-generater-wroy.vercel.app/qr/9304619487@fam/{amount}/KHAN%20STORE',
        'features': {
            'time_window': f'Only verifies RECEIVED payments from last {CONFIG["TIME_WINDOW_MINUTES"]} minutes',
            'fast_response': '⚡ Checks every 0.5 seconds (ULTRA FAST)',
            'real_time': 'SSE streaming for live updates',
            'auto_filter': 'Automatically skips PAID transactions',
            'instant_verify': 'Payment verified in under 1 second'
        },
        'endpoints': {
            'generate_qr': {
                'method': 'GET',
                'path': '/generate-qr',
                'params': {'amount': 'required'},
                'example': '/generate-qr?amount=100',
                'qr_url': 'https://upi-qrcode-generater-wroy.vercel.app/qr/9304619487@fam/100/KHAN%20STORE'
            },
            'verify_payment': {
                'method': 'POST/GET',
                'path': '/verify-payment',
                'params': {'amount': 'required', 'time_window': 'optional (default 5)'},
                'example': '/verify-payment?amount=100&time_window=5'
            },
            'verify_realtime': {
                'method': 'GET',
                'path': '/verify-realtime',
                'params': {'amount': 'required', 'time_window': 'optional (default 5)'},
                'example': '/verify-realtime?amount=100&time_window=5'
            },
            'verify_last_payment': {
                'method': 'GET',
                'path': '/verify-last-payment',
                'params': {'amount': 'required', 'time_window': 'optional (default 5)'},
                'example': '/verify-last-payment?amount=100&time_window=5',
                'description': '⚡ FASTEST - One-time check (under 1 second)'
            },
            'debug_emails': {
                'method': 'GET',
                'path': '/debug-emails',
                'description': 'Shows emails with time stamps and transaction types'
            },
            'health': {
                'method': 'GET',
                'path': '/health'
            },
            'change_credentials': {
                'method': 'POST',
                'path': '/change-credentials',
                'params': {'email': 'optional', 'password': 'optional (16 digits)'}
            },
            'change_password': {
                'method': 'POST',
                'path': '/change-password',
                'params': {'password': 'required (16 digits)'}
            },
            'change_email': {
                'method': 'POST',
                'path': '/change-email',
                'params': {'email': 'required'}
            }
        },
        'test_commands': {
            'generate_qr': 'curl "http://127.0.0.1:5000/generate-qr?amount=100"',
            'fast': 'curl "http://127.0.0.1:5000/verify-last-payment?amount=100"',
            'realtime': 'curl "http://127.0.0.1:5000/verify-realtime?amount=100"',
            'debug': 'curl "http://127.0.0.1:5000/debug-emails"'
        }
    })

if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("⚡ UPI PAYMENT VERIFIER - ULTRA FAST VERSION")
    logger.info("=" * 50)
    logger.info(f"📧 Gmail: {CONFIG['GMAIL_EMAIL']}")
    logger.info(f"🔐 App Password: {CONFIG['GMAIL_APP_PASSWORD']}")
    logger.info(f"📱 UPI ID: {CONFIG['UPI_ID']}")
    logger.info(f"🏪 Payee: {CONFIG['PAYEE_NAME']}")
    logger.info(f"⏰ Time Window: {CONFIG['TIME_WINDOW_MINUTES']} minutes")
    logger.info(f"⚡ Poll Interval: {CONFIG['POLL_INTERVAL']} seconds (ULTRA FAST)")
    logger.info(f"🔄 Type: ONLY RECEIVED transactions")
    logger.info(f"🌐 Server: http://127.0.0.1:{CONFIG['PORT']}")
    logger.info("=" * 50)
    logger.info("📌 QR URL FORMAT:")
    logger.info(f"  https://upi-qrcode-generater-wroy.vercel.app/qr/9304619487@fam/{{amount}}/KHAN%20STORE")
    logger.info("=" * 50)
    logger.info("📌 TEST NOW:")
    logger.info(f"  🏪 /generate-qr?amount=100 (Generate QR)")
    logger.info(f"  ⚡ /verify-last-payment?amount=100 (FASTEST - under 1s)")
    logger.info(f"  ✅ /verify-payment?amount=100 (Polling)")
    logger.info(f"  ⭐ /verify-realtime?amount=100 (Realtime)")
    logger.info("=" * 50)
    
    app.run(
        host='0.0.0.0',
        port=CONFIG['PORT'],
        debug=False,
        threaded=True
    )