'use client';

import { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

export default function SubscribeSuccess() {
  const router = useRouter();
  const params = useSearchParams();
  const [status, setStatus] = useState('processing');
  const [message, setMessage] = useState('');

  useEffect(() => {
    const paymentKey = params.get('paymentKey');
    const orderId = params.get('orderId');
    const amount = params.get('amount');
    const plan = params.get('plan');

    if (!paymentKey || !orderId || !amount || !plan) {
      setStatus('error');
      setMessage('잘못된 접근입니다');
      return;
    }

    const token = localStorage.getItem('token');

    fetch('http://localhost:8000/api/billing/confirm', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        payment_key: paymentKey,
        order_id: orderId,
        amount: parseInt(amount),
        plan,
      }),
    })
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '구독 처리 실패');
        setStatus('success');
        setMessage('구독이 시작되었습니다!');
        setTimeout(() => router.push('/'), 2000);
      })
      .catch((err) => {
        setStatus('error');
        setMessage(err.message);
      });
  }, [params, router]);

  return (
    <main className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow p-8 w-full max-w-sm text-center">
        {status === 'processing' && (
          <>
            <p className="text-gray-900 font-medium mb-1">결제 처리 중...</p>
            <p className="text-gray-400 text-sm">잠시만 기다려주세요</p>
          </>
        )}
        {status === 'success' && (
          <>
            <p className="text-green-600 font-medium mb-1">{message}</p>
            <p className="text-gray-400 text-sm">메인 페이지로 이동합니다</p>
          </>
        )}
        {status === 'error' && (
          <>
            <p className="text-red-600 font-medium mb-1">처리 실패</p>
            <p className="text-gray-400 text-sm">{message}</p>
          </>
        )}
      </div>
    </main>
  );
}