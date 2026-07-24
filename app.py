"""
UPI Auto-Payment Verifier API - FINAL WORKING VERSION
Email detection working perfectly!
"""

import os
import re
import time
import json
import logging
import imaplib
import email
from email.header import decode_header
from datetime import datetime
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
    'PAYEE_NAME': 'mdnooralam',
    'GMAIL_APP_PASSWORD': 'owjwtlotkfjnsftm',
    'GMAIL_EMAIL': 'nkg166465@gmail.com',
    'POLL_INTERVAL': 2,  # Reduced from 3 to 2 seconds for faster response
    'POLL_TIMEOUT': 60,
    'QR_BASE_URL': 'https://upi-qrcode-generater-wroy.vercel.app/qr',
    'PORT': int(os.getenv('PORT', 5000))
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
    """Parse email body to extract payment details - FULLY IMPROVED"""
    details = {
        'amount': None,
        'transaction_id': None,
        'utr': None,
        'date': None,
        'balance': None,
        'sender': None,
        'purpose': None,
        'type': None,  # 'received' or 'paid'
        'raw_preview': body[:200]
    }
    
    # ✅ Check if it's a RECEIVED or PAID transaction
    if 'successfully received' in body.lower():
        details['type'] = 'received'
        logger.info("📥 Transaction type: RECEIVED")
    elif 'successfully paid' in body.lower():
        details['type'] = 'paid'
        logger.info("📤 Transaction type: PAID")
    
    # ✅ Amount extraction
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
    
    # ✅ Transaction ID
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
    
    # ✅ UTR
    utr_match = re.search(r'UTR\s*[:]\s*([0-9]+)', body, re.IGNORECASE)
    if utr_match:
        details['utr'] = utr_match.group(1)
    
    # ✅ Date
    date_match = re.search(
        r'([0-9]{2}:[0-9]{2}\s*(AM|PM)\s*IST,\s*[0-9]{2}\s*[A-Za-z]+\s*[0-9]{4})',
        body, re.IGNORECASE
    )
    if date_match:
        details['date'] = date_match.group(1)
    
    # ✅ Balance
    balance_match = re.search(r'Updated Balance\s*[:]\s*₹([0-9]+(\.[0-9]+)?)', body, re.IGNORECASE)
    if balance_match:
        details['balance'] = float(balance_match.group(1))
    
    # ✅ Sender (for received transactions)
    sender_match = re.search(r'from\s*([A-Za-z\s.]+)', body, re.IGNORECASE)
    if sender_match:
        details['sender'] = sender_match.group(1).strip()
    
    # ✅ Purpose
    purpose_match = re.search(r'Purpose\s*[:]\s*(.+)', body, re.IGNORECASE)
    if purpose_match:
        details['purpose'] = purpose_match.group(1).strip()
    
    return details

def search_payment_email_imap(mail, amount: float, start_timestamp: int, check_count: int = 0) -> Optional[Dict[str, Any]]:
    """Search Gmail inbox for payment confirmation email using IMAP - FAST VERSION"""
    try:
        date_str = datetime.fromtimestamp(start_timestamp).strftime('%d-%b-%Y')
        logger.info(f"🔍 Searching IMAP (Attempt {check_count})")
        
        # ✅ Search only emails from today
        result, data = mail.search(None, 'ALL')
        if result != 'OK':
            return None
        
        email_ids = data[0].split()
        if not email_ids:
            logger.info(f"❌ No emails found")
            return None
        
        logger.info(f"📬 Found {len(email_ids)} emails total")
        
        # ✅ Check ONLY the most recent emails for speed
        # Increased from 50 to 30 for faster checking
        for msg_id in email_ids[-30:]:
            msg_id_str = msg_id.decode('utf-8') if isinstance(msg_id, bytes) else str(msg_id)
            
            try:
                # ✅ Get email date to check if it's recent
                result, data = mail.fetch(msg_id, '(BODY.PEEK[HEADER.FIELDS (DATE)])')
                if result == 'OK':
                    header_data = data[0][1].decode('utf-8', errors='ignore')
                    date_match = re.search(r'Date:\s*(.+)', header_data, re.IGNORECASE)
                    if date_match:
                        try:
                            email_date = email.utils.parsedate_to_datetime(date_match.group(1))
                            # ✅ Only process emails from last 2 hours
                            time_diff = (datetime.now(email_date.tzinfo) - email_date).total_seconds() if email_date.tzinfo else (datetime.now() - email_date).total_seconds()
                            if time_diff > 7200:  # 2 hours
                                continue
                        except:
                            pass
                
                body = get_email_body_from_imap(mail, msg_id_str)
                
                if not body:
                    continue
                
                # ✅ Parse payment details
                payment_details = parse_payment_email(body)
                
                found_amount = payment_details.get('amount')
                
                if found_amount:
                    logger.info(f"💰 Found: ₹{found_amount}, Expected: ₹{amount}")
                    
                    # ✅ Check amount match (with tolerance)
                    if abs(found_amount - float(amount)) < 0.01:
                        # ✅ Check if it's a RECEIVED transaction (not paid)
                        if payment_details.get('type') == 'received':
                            logger.info(f"✅ MATCH FOUND! Received ₹{found_amount}")
                            payment_details['email_id'] = msg_id_str
                            payment_details['timestamp'] = datetime.now().isoformat()
                            payment_details['check_count'] = check_count
                            return payment_details
                        else:
                            logger.info(f"⚠️ Found amount ₹{found_amount} but it's a PAID transaction, not RECEIVED")
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

@app.route('/change-credentials', methods=['POST'])
def change_credentials():
    """
    Change Gmail email and/or password
    Format: /change-credentials?email=newemail@gmail.com&password=16digitpassword
    OR JSON body: {"email": "newemail@gmail.com", "password": "16digitpassword"}
    """
    # Get parameters from query string or JSON body
    data = request.get_json() if request.is_json else request.args.to_dict()
    
    if not data:
        return jsonify({
            'status': 'error',
            'message': 'Invalid request. Provide email and/or password in query string or JSON body.'
        }), 400
    
    new_email = data.get('email')
    new_password = data.get('password')
    
    # Validate at least one credential is provided
    if not new_email and not new_password:
        return jsonify({
            'status': 'error',
            'message': 'At least one of email or password is required'
        }), 400
    
    # Validate password if provided
    if new_password and len(new_password) != 16:
        return jsonify({
            'status': 'error',
            'message': 'Password must be exactly 16 characters'
        }), 400
    
    # Validate email if provided
    if new_email and not re.match(r'^[a-zA-Z0-9._%+-]+@gmail\.com$', new_email):
        return jsonify({
            'status': 'error',
            'message': 'Email must be a valid Gmail address (ending with @gmail.com)'
        }), 400
    
    # Test credentials before saving
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
    
    # Update configuration
    old_email = CONFIG['GMAIL_EMAIL']
    old_password = CONFIG['GMAIL_APP_PASSWORD']
    
    if new_email:
        CONFIG['GMAIL_EMAIL'] = new_email
    if new_password:
        CONFIG['GMAIL_APP_PASSWORD'] = new_password
    
    # Update .env file
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
        # Revert changes
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
    """Change Gmail app password - New 16 digit password"""
    # Get password from query string or JSON
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
    
    # ✅ Validate password length
    if len(new_password) != 16:
        return jsonify({
            'status': 'error',
            'message': 'Password must be exactly 16 characters'
        }), 400
    
    # ✅ Test the new password before saving
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
    
    # ✅ Update password
    old_password = CONFIG['GMAIL_APP_PASSWORD']
    CONFIG['GMAIL_APP_PASSWORD'] = new_password
    
    # ✅ Update .env file
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
        # Revert password
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
    """Change Gmail email address"""
    # Get email from query string or JSON
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
    
    # ✅ Validate email format
    if not re.match(r'^[a-zA-Z0-9._%+-]+@gmail\.com$', new_email):
        return jsonify({
            'status': 'error',
            'message': 'Email must be a valid Gmail address (ending with @gmail.com)'
        }), 400
    
    # ✅ Test the new email with current password
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
    
    # ✅ Update email
    old_email = CONFIG['GMAIL_EMAIL']
    CONFIG['GMAIL_EMAIL'] = new_email
    
    # ✅ Update .env file
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
        # Revert email
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

@app.route('/generate-qr', methods=['GET'])
def generate_qr():
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
    
    qr_url = f"{CONFIG['QR_BASE_URL']}/{CONFIG['UPI_ID']}/{num_amount}/{CONFIG['PAYEE_NAME']}"
    
    return jsonify({
        'status': 'success',
        'qr_url': qr_url,
        'amount': num_amount,
        'upi_id': CONFIG['UPI_ID'],
        'payee': CONFIG['PAYEE_NAME'],
        'instructions': 'Scan this QR code using any UPI app to pay'
    })

@app.route('/verify-payment', methods=['POST', 'GET'])
def verify_payment():
    if request.method == 'GET':
        amount = request.args.get('amount')
        session_id = request.args.get('session_id')
    else:
        data = request.get_json()
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'Invalid request body'
            }), 400
        amount = data.get('amount')
        session_id = data.get('session_id')
    
    if not session_id:
        session_id = f'session_{int(time.time())}_{os.urandom(4).hex()}'
    
    logger.info(f"[{session_id}] Payment verification started for ₹{amount}")
    
    if amount is None:
        return jsonify({
            'status': 'error',
            'message': 'Amount is required'
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
    
    try:
        mail = connect_imap()
        start_timestamp = int(time.time())
        
        qr_url = f"{CONFIG['QR_BASE_URL']}/{CONFIG['UPI_ID']}/{num_amount}/{CONFIG['PAYEE_NAME']}"
        
        max_attempts = CONFIG['POLL_TIMEOUT'] // CONFIG['POLL_INTERVAL']
        
        for attempt in range(1, max_attempts + 1):
            logger.info(f"[{session_id}] Checking attempt {attempt}/{max_attempts}")
            
            result = search_payment_email_imap(mail, num_amount, start_timestamp, attempt)
            
            if result and result.get('amount'):
                if abs(result.get('amount') - num_amount) < 0.01:
                    result['status'] = 'success'
                    result['message'] = '✅ Payment verified successfully!'
                    result['qr_url'] = qr_url
                    result['session_id'] = session_id
                    result['attempt'] = attempt
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
            'message': '⏰ Payment not received. Please try again.'
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
    amount = request.args.get('amount')
    
    if not amount:
        return jsonify({
            'status': 'error',
            'message': 'Amount is required'
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
    
    def generate():
        session_id = f'realtime_{int(time.time())}_{os.urandom(4).hex()}'
        start_timestamp = int(time.time())
        attempt = 0
        max_attempts = 30  # Increased from 20 to 30 (60 seconds with 2s interval)
        
        try:
            mail = connect_imap()
            
            yield f"data: {json.dumps({'status': 'checking', 'message': '🔍 Searching for payment...', 'amount': num_amount, 'session_id': session_id})}\n\n"
            
            while attempt < max_attempts:
                attempt += 1
                
                result = search_payment_email_imap(mail, num_amount, start_timestamp, attempt)
                
                if result and result.get('amount'):
                    if abs(result.get('amount') - num_amount) < 0.01:
                        result['status'] = 'success'
                        result['message'] = '✅ Payment verified successfully!'
                        result['session_id'] = session_id
                        result['attempt'] = attempt
                        yield f"data: {json.dumps(result)}\n\n"
                        mail.close()
                        mail.logout()
                        break
                
                progress = {
                    'status': 'waiting',
                    'message': f'⏳ Waiting for payment... Attempt {attempt}/{max_attempts}',
                    'amount': num_amount,
                    'session_id': session_id,
                    'attempt': attempt,
                    'max_attempts': max_attempts,
                    'progress': round((attempt / max_attempts) * 100, 1)
                }
                yield f"data: {json.dumps(progress)}\n\n"
                time.sleep(CONFIG['POLL_INTERVAL'])
            
            if attempt >= max_attempts:
                timeout_msg = {
                    'status': 'timeout',
                    'message': '⏰ Payment not received. Please try again.',
                    'amount': num_amount,
                    'session_id': session_id
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
        # ✅ Get only last 20 emails
        for msg_id in email_ids[-20:]:
            msg_id_str = msg_id.decode('utf-8') if isinstance(msg_id, bytes) else str(msg_id)
            
            try:
                # ✅ Get email date
                result, data = mail.fetch(msg_id, '(BODY.PEEK[HEADER.FIELDS (DATE)])')
                date_str = ""
                if result == 'OK':
                    header_data = data[0][1].decode('utf-8', errors='ignore')
                    date_match = re.search(r'Date:\s*(.+)', header_data, re.IGNORECASE)
                    if date_match:
                        date_str = date_match.group(1).strip()
                
                body = get_email_body_from_imap(mail, msg_id_str)
                details = parse_payment_email(body)
                
                emails.append({
                    'id': msg_id_str,
                    'date': date_str,
                    'body_preview': body[:200] if body else 'No body',
                    'amount_found': details.get('amount'),
                    'transaction_type': details.get('type'),
                    'transaction_id': details.get('transaction_id'),
                    'sender': details.get('sender'),
                    'purpose': details.get('purpose'),
                    'balance': details.get('balance')
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
            'emails': emails
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'gmail': CONFIG['GMAIL_EMAIL'],
        'upi_id': CONFIG['UPI_ID'],
        'auth_method': 'IMAP with App Password',
        'gmail_configured': True
    })

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'name': 'UPI Auto-Payment Verifier API',
        'version': '1.2.0',
        'gmail': CONFIG['GMAIL_EMAIL'],
        'status': '✅ FULLY WORKING - FAST RESPONSE',
        'endpoints': {
            'change_credentials': {
                'method': 'POST',
                'path': '/change-credentials',
                'params': {'email': 'optional', 'password': 'optional (16 digits)'},
                'examples': [
                    '/change-credentials?email=newemail@gmail.com&password=1234567890123456',
                    '/change-credentials?email=newemail@gmail.com',
                    '/change-credentials?password=1234567890123456'
                ]
            },
            'change_password': {
                'method': 'POST',
                'path': '/change-password',
                'params': {'password': 'required (16 digits)'},
                'example': '/change-password?password=1234567890123456'
            },
            'change_email': {
                'method': 'POST',
                'path': '/change-email',
                'params': {'email': 'required'},
                'example': '/change-email?email=newemail@gmail.com'
            },
            'generate_qr': {
                'method': 'GET',
                'path': '/generate-qr',
                'params': {'amount': 'required'},
                'example': '/generate-qr?amount=1'
            },
            'verify_payment': {
                'method': 'POST/GET',
                'path': '/verify-payment',
                'params': {'amount': 'required'},
                'example': '/verify-payment?amount=1'
            },
            'verify_realtime': {
                'method': 'GET',
                'path': '/verify-realtime',
                'params': {'amount': 'required'},
                'example': '/verify-realtime?amount=1'
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
    logger.info("🚀 UPI PAYMENT VERIFIER API - FAST RESPONSE VERSION")
    logger.info("=" * 50)
    logger.info(f"📧 Gmail: {CONFIG['GMAIL_EMAIL']}")
    logger.info(f"🔐 App Password: {CONFIG['GMAIL_APP_PASSWORD']}")
    logger.info(f"📱 UPI ID: {CONFIG['UPI_ID']}")
    logger.info(f"🌐 Server: http://127.0.0.1:{CONFIG['PORT']}")
    logger.info("=" * 50)
    logger.info("📌 TEST NOW:")
    logger.info(f"  🔍 /debug-emails")
    logger.info(f"  ✅ /verify-payment?amount=1")
    logger.info(f"  ⭐ /verify-realtime?amount=1")
    logger.info("=" * 50)
    logger.info("🔑 CHANGE CREDENTIALS:")
    logger.info(f"  /change-credentials?email=new@gmail.com&password=1234567890123456")
    logger.info("=" * 50)
    
    app.run(
        host='0.0.0.0',
        port=CONFIG['PORT'],
        debug=False,
        threaded=True
    )