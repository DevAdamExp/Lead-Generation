'use client';

import { useEffect } from 'react';

interface ToastProps {
  message: string | null;
  type: 'success' | 'error' | 'info';
  visible: boolean;
  onClose: () => void;
}

export default function Toast({ message, type, visible, onClose }: ToastProps) {
  useEffect(() => {
    if (visible && message) {
      const t = setTimeout(onClose, 5000);
      return () => clearTimeout(t);
    }
  }, [visible, message, onClose]);

  if (!visible || !message) return null;

  return (
    <div className={`toast toast-${type}`} role="alert">
      <span className="toast-icon">
        {type === 'success' ? '\u2713' : type === 'error' ? '\u2717' : '\u2139'}
      </span>
      <span className="toast-message">{message}</span>
      <button className="toast-close" onClick={onClose} aria-label="Close">&times;</button>
    </div>
  );
}
