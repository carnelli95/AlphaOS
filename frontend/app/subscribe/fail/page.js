'use client';

import { useSearchParams } from 'next/navigation';
import Link from 'next/link';

export default function SubscribeFail() {
  const params = useSearchParams();
  const message = params.get('message') || '결제가 취소되었습니다';

  return (
    <main className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow p-8 w-full max-w-sm text-center">
        <p className="text-red-600 font-medium mb-1">결제 실패</p>
        <p className="text-gray-400 text-sm mb-6">{message}</p>
        <Link
          href="/subscribe"
          className="inline-block bg-blue-600 text-white rounded-lg px-6 py-2.5 text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          다시 시도하기
        </Link>
      </div>
    </main>
  );
}