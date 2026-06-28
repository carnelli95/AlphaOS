'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';

const TOSS_CLIENT_KEY = process.env.NEXT_PUBLIC_TOSS_CLIENT_KEY;

const PLANS = [
  { id: 'monthly', name: '월간 구독', price: 9900, unit: '월' },
  { id: 'yearly', name: '연간 구독', price: 99000, unit: '년', badge: '17% 할인' },
];

export default function Subscribe() {
  const router = useRouter();
  const [selectedPlan, setSelectedPlan] = useState('monthly');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) router.push('/login');
  }, [router]);

  const handleSubscribe = async () => {
    setError('');
    setLoading(true);

    try {
      const { loadTossPayments } = await import('@tosspayments/tosspayments-sdk');
      const tossPayments = await loadTossPayments(TOSS_CLIENT_KEY);

      const token = localStorage.getItem('token');
      const payload = JSON.parse(atob(token.split('.')[1]));
      const customerKey = `user-${payload.sub.replace('@', '-at-')}`;

      const plan = PLANS.find(p => p.id === selectedPlan);
      const orderId = `order_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

      // 결제 정보를 임시 저장 (success 페이지에서 사용)
      sessionStorage.setItem('pending_plan', selectedPlan);

      const payment = tossPayments.payment({ customerKey });

      await payment.requestPayment({
        method: 'CARD',
        amount: { currency: 'KRW', value: plan.price },
        orderId,
        orderName: `퀀트 스크리너 ${plan.name}`,
        customerEmail: payload.sub,
        successUrl: `${window.location.origin}/subscribe/success?plan=${selectedPlan}`,
        failUrl: `${window.location.origin}/subscribe/fail`,
      });
    } catch (err) {
      setError(err.message || '결제 요청 실패');
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-2xl mx-auto">
        <h1 className="text-2xl font-bold text-gray-900 mb-2">구독 플랜 선택</h1>
        <p className="text-gray-400 mb-8">퀀트 스크리너의 모든 기능을 이용해보세요</p>

        {error && (
          <div className="bg-red-50 text-red-600 text-sm rounded-lg px-4 py-3 mb-4">
            {error}
          </div>
        )}

        <div className="grid grid-cols-2 gap-4 mb-8">
          {PLANS.map((plan) => (
            <button
              key={plan.id}
              onClick={() => setSelectedPlan(plan.id)}
              className={`relative text-left p-6 rounded-xl border-2 transition-colors ${
                selectedPlan === plan.id
                  ? 'border-blue-600 bg-blue-50'
                  : 'border-gray-200 bg-white hover:border-gray-300'
              }`}
            >
              {plan.badge && (
                <span className="absolute top-4 right-4 bg-green-100 text-green-700 text-xs font-medium px-2 py-1 rounded">
                  {plan.badge}
                </span>
              )}
              <p className="text-sm text-gray-500 mb-1">{plan.name}</p>
              <p className="text-2xl font-bold text-gray-900">
                {plan.price.toLocaleString()}원
                <span className="text-sm text-gray-400 font-normal"> / {plan.unit}</span>
              </p>
            </button>
          ))}
        </div>

        <div className="bg-white rounded-xl shadow p-6 mb-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">포함된 기능</h2>
          <ul className="space-y-2 text-sm text-gray-600">
            <li>✓ 코스피 전 종목 PBR/ROE/PER 스크리닝</li>
            <li>✓ 종목별 상세 재무 분석 차트</li>
            <li>✓ 매일 자동 업데이트되는 최신 데이터</li>
            <li>✓ 무제한 필터링 및 검색</li>
          </ul>
        </div>

        <button
          onClick={handleSubscribe}
          disabled={loading}
          className="w-full bg-blue-600 text-white rounded-lg py-3.5 text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
        >
          {loading ? '처리 중...' : '결제하고 구독 시작하기'}
        </button>

        <p className="text-center text-xs text-gray-400 mt-4">
          현재는 매 결제 주기마다 직접 결제가 필요합니다 (자동결제 준비 중)
        </p>
      </div>
    </main>
  );
}