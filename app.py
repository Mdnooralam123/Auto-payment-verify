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
    'POLL_INTERVAL': 0.5,
    'POLL_TIMEOUT': 60,
    'QR_BASE_URL': 'https://upi-qrcode-generater-wroy.vercel.app/qr',
    'PORT': int(os.getenv('PORT', 5000)),
    'TIME_WINDOW_MINUTES': 30
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
    """Parse email body to extract payment details - FINAL FIX for FamPay"""
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
    
    # 🔥 CRITICAL FIX: Better transaction type detection
    body_lower = body.lower()
    
    # Check for received transaction - FamPay specific
    if 'successfully received' in body_lower or 'you have successfully received' in body_lower:
        details['type'] = 'received'
        logger.info("📥 Transaction type: RECEIVED")
    elif 'successfully paid' in body_lower or 'paid to' in body_lower or 'debited' in body_lower:
        details['type'] = 'paid'
        logger.info("📤 Transaction type: PAID - SKIPPING")
        return details
    else:
        details['type'] = 'unknown'
        logger.info("❓ Unknown transaction type")
    
    # 🔥 CRITICAL FIX: Better amount extraction for FamPay format
    # FamPay format: "You have successfully received\n₹1.0" or "received ₹1.0"
    amount_patterns = [
        r'received\s*₹\s*([0-9]+(?:\.[0-9]+)?)',  # received ₹1.0
        r'You have successfully received\s*₹\s*([0-9]+(?:\.[0-9]+)?)',  # You have successfully received ₹1.0
        r'₹\s*([0-9]+(?:\.[0-9]+)?)',  # ₹100 or ₹100.00
        r'Rs\.?\s*([0-9]+(?:\.[0-9]+)?)',  # Rs 100 or Rs.100
        r'INR\s*([0-9]+(?:\.[0-9]+)?)',  # INR 100
        r'Amount\s*[:]\s*₹\s*([0-9]+(?:\.[0-9]+)?)',
        r'Amount\s*[:]\s*Rs\.?\s*([0-9]+(?:\.[0-9]+)?)',
        r'([0-9]+(?:\.[0-9]+)?)\s*INR',
        r'([0-9]+(?:\.[0-9]+)?)\s*Rs\.?',
        r'credited with\s*₹\s*([0-9]+(?:\.[0-9]+)?)',
    ]
    
    for pattern in amount_patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            details['amount'] = float(match.group(1))
            logger.info(f"💰 Found amount: ₹{details['amount']}")
            break
    
    # 🔥 CRITICAL FIX: Better transaction ID extraction
    tx_patterns = [
        r'Transaction ID\s*[:]\s*([A-Z0-9]{10,})',
        r'Txn ID\s*[:]\s*([A-Z0-9]{10,})',
        r'Txn\s*[:]\s*([A-Z0-9]{10,})',
        r'Transaction\s*ID\s*[:]\s*([A-Z0-9]{10,})',
        r'with transaction id\s*([A-Z0-9]{10,})',
        r'txnid\s*[:]\s*([A-Z0-9]{10,})',
        r'ID\s*[:]\s*([A-Z0-9]{10,})',
    ]
    for pattern in tx_patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            details['transaction_id'] = match.group(1)
            logger.info(f"📋 Transaction ID: {details['transaction_id']}")
            break
    
    # 🔥 CRITICAL FIX: UTR extraction
    utr_patterns = [
        r'UTR\s*[:]\s*([0-9]{10,})',
        r'UTR\s*No\s*[:]\s*([0-9]{10,})',
        r'UTR Number\s*[:]\s*([0-9]{10,})',
    ]
    for pattern in utr_patterns:
        utr_match = re.search(pattern, body, re.IGNORECASE)
        if utr_match:
            details['utr'] = utr_match.group(1)
            logger.info(f"🔢 UTR: {details['utr']}")
            break
    
    # 🔥 CRITICAL FIX: Date extraction
    date_patterns = [
        r'Date\s*[:]\s*([0-9]{2}:[0-9]{2}\s*(AM|PM)\s*IST,\s*[0-9]{2}\s*[A-Za-z]+\s*[0-9]{4})',
        r'([0-9]{2}:[0-9]{2}\s*(AM|PM)\s*IST,\s*[0-9]{2}\s*[A-Za-z]+\s*[0-9]{4})',
        r'Date\s*[:]\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s*[0-9]{2}:[0-9]{2}\s*(AM|PM))',
        r'dated\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s*at\s*[0-9]{2}:[0-9]{2}\s*(AM|PM))',
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            details['date'] = match.group(1)
            logger.info(f"📅 Date found: {details['date']}")
            break
    
    # Parse datetime from the extracted date string
    if details['date']:
        try:
            date_str = details['date']
            # Extract time
            time_match = re.search(r'([0-9]{1,2}):([0-9]{2})\s*(AM|PM)', date_str, re.IGNORECASE)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))
                ampm = time_match.group(3).upper()
                
                if ampm == 'PM' and hour != 12:
                    hour += 12
                elif ampm == 'AM' and hour == 12:
                    hour = 0
                
                now = datetime.now()
                payment_time = datetime(now.year, now.month, now.day, hour, minute)
                
                # If payment time is in future, it was yesterday
                if payment_time > now:
                    payment_time = payment_time - timedelta(days=1)
                
                details['payment_datetime'] = payment_time
                details['time_diff_minutes'] = round((now - payment_time).total_seconds() / 60, 1)
                logger.info(f"⏰ Payment at: {payment_time.strftime('%H:%M')}, {details['time_diff_minutes']} minutes ago")
        except Exception as e:
            logger.warning(f"Could not parse date: {e}")
    
    # Balance
    balance_match = re.search(r'Updated Balance\s*[:]\s*₹\s*([0-9]+(?:\.[0-9]+)?)', body, re.IGNORECASE)
    if balance_match:
        details['balance'] = float(balance_match.group(1))
    
    # Sender
    sender_match = re.search(r'from\s*([A-Za-z\s.]+)', body, re.IGNORECASE)
    if sender_match:
        details['sender'] = sender_match.group(1).strip()[:50]
    
    # Purpose
    purpose_match = re.search(r'Purpose\s*[:]\s*(.+)', body, re.IGNORECASE)
    if purpose_match:
        details['purpose'] = purpose_match.group(1).strip()[:100]
    
    return details

def search_payment_email_imap(mail, amount: float, start_timestamp: int, check_count: int = 0, time_window_minutes: int = 30) -> Optional[Dict[str, Any]]:
    """ULTRA FAST - Search emails within time window"""
    try:
        time_window_minutes = int(time_window_minutes)
        logger.info(f"⚡ ULTRA FAST Search (Attempt {check_count}) - Last {time_window_minutes} minutes")
        
        # 🔥 FIX: Search ALL emails and filter by date
        result, data = mail.search(None, 'ALL')
        if result != 'OK':
            return None
        
        email_ids = data[0].split()
        if not email_ids:
            logger.info(f"❌ No emails found")
            return None
        
        # Check last 100 emails
        recent_ids = email_ids[-100:] if len(email_ids) > 100 else email_ids
        logger.info(f"📧 Checking {len(recent_ids)} recent emails")
        
        processed_ids = set()
        
        for msg_id in reversed(recent_ids):  # Check newest first
            msg_id_str = msg_id.decode('utf-8') if isinstance(msg_id, bytes) else str(msg_id)
            
            if msg_id_str in processed_ids:
                continue
            processed_ids.add(msg_id_str)
            
            try:
                # Check email date
                result, data = mail.fetch(msg_id, '(BODY.PEEK[HEADER.FIELDS (DATE)])')
                if result == 'OK':
                    header_data = data[0][1].decode('utf-8', errors='ignore')
                    date_match = re.search(r'Date:\s*(.+)', header_data, re.IGNORECASE)
                    if date_match:
                        try:
                            email_date = email.utils.parsedate_to_datetime(date_match.group(1))
                            if email_date.tzinfo:
                                now = datetime.now(email_date.tzinfo)
                            else:
                                now = datetime.now()
                            time_diff = (now - email_date).total_seconds() / 60
                            
                            # Skip if email is older than time window
                            if time_diff > time_window_minutes:
                                continue
                        except:
                            pass
                
                # Get full email body
                body = get_email_body_from_imap(mail, msg_id_str)
                
                if not body:
                    continue
                
                # Parse payment details
                payment_details = parse_payment_email(body)
                
                # Skip if not received transaction
                if payment_details.get('type') != 'received':
                    continue
                
                found_amount = payment_details.get('amount')
                
                if found_amount is not None:
                    logger.info(f"💰 Found: ₹{found_amount}, Expected: ₹{amount}")
                    
                    # Amount matching
                    if abs(found_amount - float(amount)) < 0.01:
                        # Check time difference
                        payment_datetime = payment_details.get('payment_datetime')
                        if payment_datetime:
                            time_diff = payment_details.get('time_diff_minutes', 0)
                            if time_diff <= time_window_minutes:
                                logger.info(f"✅✅✅ MATCH FOUND! Received ₹{found_amount} ({time_diff:.1f} minutes ago)")
                                payment_details['email_id'] = msg_id_str
                                payment_details['timestamp'] = datetime.now().isoformat()
                                payment_details['check_count'] = check_count
                                return payment_details
                        else:
                            # If no payment datetime, use email date
                            try:
                                result, data = mail.fetch(msg_id, '(BODY.PEEK[HEADER.FIELDS (DATE)])')
                                if result == 'OK':
                                    header_data = data[0][1].decode('utf-8', errors='ignore')
                                    date_match = re.search(r'Date:\s*(.+)', header_data, re.IGNORECASE)
                                    if date_match:
                                        email_date = email.utils.parsedate_to_datetime(date_match.group(1))
                                        if email_date.tzinfo:
                                            now = datetime.now(email_date.tzinfo)
                                        else:
                                            now = datetime.now()
                                        time_diff = (now - email_date).total_seconds() / 60
                                        if time_diff <= time_window_minutes:
                                            logger.info(f"✅✅✅ MATCH FOUND! Received ₹{found_amount}")
                                            payment_details['email_id'] = msg_id_str
                                            payment_details['timestamp'] = datetime.now().isoformat()
                                            payment_details['check_count'] = check_count
                                            payment_details['time_diff_minutes'] = round(time_diff, 1)
                                            return payment_details
                            except:
                                pass
                
            except Exception as e:
                logger.warning(f"Error processing email: {e}")
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
        time_window = int(time_window) if time_window else CONFIG['TIME_WINDOW_MINUTES']
    except ValueError as e:
        return jsonify({
            'status': 'error',
            'message': f'Invalid input: {str(e)}'
        }), 400
    
    qr_url = f"https://upi-qrcode-generater-wroy.vercel.app/qr/9304619487@fam/{num_amount}/KHAN%20STORE"
    
    # ⚡ FAST RESPONSE: Immediate check
    try:
        mail = connect_imap()
        immediate_result = search_payment_email_imap(mail, num_amount, int(time.time()), 0, time_window)
        if immediate_result and immediate_result.get('amount'):
            if abs(immediate_result.get('amount') - num_amount) < 0.01:
                immediate_result['status'] = 'success'
                immediate_result['message'] = f'✅ Payment verified instantly! (Received {immediate_result.get("time_diff_minutes", 0)} minutes ago)'
                immediate_result['qr_url'] = qr_url
                immediate_result['session_id'] = session_id
                immediate_result['response_time'] = 'Instant'
                immediate_result['time_window_minutes'] = time_window
                mail.close()
                mail.logout()
                return jsonify(immediate_result)
        mail.close()
        mail.logout()
    except Exception as e:
        logger.warning(f"Immediate check failed: {e}")
    
    try:
        mail = connect_imap()
        start_timestamp = int(time.time())
        
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
            'status': 'not_found',
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
    """ULTRA FAST Realtime verification"""
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
        time_window = int(time_window) if time_window else CONFIG['TIME_WINDOW_MINUTES']
    except ValueError as e:
        return jsonify({
            'status': 'error',
            'message': f'Invalid input: {str(e)}'
        }), 400
    
    def generate():
        session_id = f'realtime_{int(time.time())}_{os.urandom(4).hex()}'
        start_timestamp = int(time.time())
        attempt = 0
        max_attempts = 120
        
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
    """⚡ FASTEST - One-time check"""
    amount = request.args.get('amount')
    time_window = request.args.get('time_window', CONFIG['TIME_WINDOW_MINUTES'])
    
    if not amount:
        return jsonify({
            'status': 'error',
            'message': 'Amount is required. Example: ?amount=1&time_window=30'
        }), 400
    
    try:
        num_amount = float(amount)
        time_window = int(time_window) if time_window else CONFIG['TIME_WINDOW_MINUTES']
    except ValueError as e:
        return jsonify({
            'status': 'error',
            'message': f'Invalid input: {str(e)}'
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
        
        for msg_id in email_ids[-30:]:
            msg_id_str = msg_id.decode('utf-8') if isinstance(msg_id, bytes) else str(msg_id)
            
            try:
                body = get_email_body_from_imap(mail, msg_id_str)
                details = parse_payment_email(body)
                
                emails.append({
                    'id': msg_id_str,
                    'amount_found': details.get('amount'),
                    'transaction_type': details.get('type'),
                    'transaction_id': details.get('transaction_id'),
                    'utr': details.get('utr'),
                    'sender': details.get('sender'),
                    'payment_time': details.get('date'),
                    'time_diff_minutes': details.get('time_diff_minutes'),
                    'is_received': details.get('type') == 'received',
                    'body_preview': body[:200] if body else 'No body'
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
                'example': '/generate-qr?amount=100'
            },
            'verify_payment': {
                'method': 'POST/GET',
                'path': '/verify-payment',
                'params': {'amount': 'required', 'time_window': 'optional (default 30)'},
                'example': '/verify-payment?amount=1&time_window=30'
            },
            'verify_realtime': {
                'method': 'GET',
                'path': '/verify-realtime',
                'params': {'amount': 'required', 'time_window': 'optional (default 30)'},
                'example': '/verify-realtime?amount=1&time_window=30'
            },
            'verify_last_payment': {
                'method': 'GET',
                'path': '/verify-last-payment',
                'params': {'amount': 'required', 'time_window': 'optional (default 30)'},
                'example': '/verify-last-payment?amount=1&time_window=30'
            },
            'debug_emails': {
                'method': 'GET',
                'path': '/debug-emails'
            },
            'health': {
                'method': 'GET',
                'path': '/health'
            }
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
    logger.info(f"⚡ Poll Interval: {CONFIG['POLL_INTERVAL']} seconds")
    logger.info(f"🌐 Server: http://127.0.0.1:{CONFIG['PORT']}")
    logger.info("=" * 50)
    logger.info("📌 TEST NOW:")
    logger.info(f"  ⚡ /verify-last-payment?amount=1&time_window=30")
    logger.info(f"  🔍 /debug-emails")
    logger.info("=" * 50)
    
    app.run(
        host='0.0.0.0',
        port=CONFIG['PORT'],
        debug=False,
        threaded=True
    )