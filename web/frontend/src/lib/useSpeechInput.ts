import { useCallback, useEffect, useRef, useState } from 'react';
import {
  collectSpeechResults,
  getSpeechRecognitionCtor,
  speechErrorMessage,
} from './speechInput';
import type { SpeechRecognitionLike } from './speechInput';

interface UseSpeechInputOptions {
  onFinal: (text: string) => void;
}

export interface SpeechInputState {
  isSupported: boolean;
  isListening: boolean;
  interimText: string;
  error: string;
  start: () => void;
  stop: () => void;
  cancel: () => void;
}

export function useSpeechInput({ onFinal }: UseSpeechInputOptions): SpeechInputState {
  const [isSupported] = useState(() => getSpeechRecognitionCtor() !== null);
  const [isListening, setIsListening] = useState(false);
  const [interimText, setInterimText] = useState('');
  const [error, setError] = useState('');
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const committedFinalsRef = useRef<Set<number>>(new Set());
  const onFinalRef = useRef(onFinal);
  const mountedRef = useRef(true);

  useEffect(() => {
    onFinalRef.current = onFinal;
  }, [onFinal]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const recognition = recognitionRef.current;
      recognitionRef.current = null;
      if (recognition) {
        try {
          recognition.abort();
        } catch {
          // 识别器已经结束时 abort 可能抛错，无需影响页面卸载。
        }
      }
    };
  }, []);

  const start = useCallback(() => {
    if (!isSupported) {
      setError('当前浏览器不支持语音输入，请使用 Chrome 或 Edge。');
      return;
    }
    if (recognitionRef.current) return;

    const Recognition = getSpeechRecognitionCtor();
    if (!Recognition) {
      setError('当前浏览器不支持语音输入，请使用 Chrome 或 Edge。');
      return;
    }

    const recognition = new Recognition();
    recognition.lang = 'zh-CN';
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    committedFinalsRef.current = new Set();

    recognition.onstart = () => {
      if (recognitionRef.current !== recognition) return;
      setIsListening(true);
      setError('');
    };

    recognition.onresult = (event) => {
      if (recognitionRef.current !== recognition) return;
      const { finalText, interimText: nextInterim, finalIndexes } = collectSpeechResults(
        event.results,
        event.resultIndex,
        committedFinalsRef.current,
      );
      finalIndexes.forEach((index) => committedFinalsRef.current.add(index));
      setInterimText(nextInterim);
      if (finalText) onFinalRef.current(finalText);
    };

    recognition.onerror = (event) => {
      if (recognitionRef.current !== recognition || event.error === 'aborted') return;
      recognitionRef.current = null;
      setError(speechErrorMessage(event.error, event.message));
      setIsListening(false);
      setInterimText('');
      try {
        recognition.abort();
      } catch {
        // 识别器已经结束时 abort 可能抛错。
      }
    };

    recognition.onend = () => {
      if (recognitionRef.current !== recognition) return;
      recognitionRef.current = null;
      setIsListening(false);
      setInterimText('');
    };

    recognitionRef.current = recognition;
    setIsListening(true);
    setInterimText('');
    setError('');

    try {
      recognition.start();
    } catch (startError) {
      recognitionRef.current = null;
      setIsListening(false);
      const detail = startError instanceof Error ? startError.message : '';
      setError(speechErrorMessage('start-failed', detail));
    }
  }, [isSupported]);

  const stop = useCallback(() => {
    const recognition = recognitionRef.current;
    if (!recognition) return;
    try {
      recognition.stop();
    } catch {
      recognitionRef.current = null;
      if (mountedRef.current) {
        setIsListening(false);
        setInterimText('');
      }
      try {
        recognition.abort();
      } catch {
        // 识别器已关闭，忽略重复 abort。
      }
    }
  }, []);

  const cancel = useCallback(() => {
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    if (mountedRef.current) {
      setIsListening(false);
      setInterimText('');
      setError('');
    }
    if (recognition) {
      try {
        recognition.abort();
      } catch {
        // 识别器已关闭，忽略重复 abort。
      }
    }
  }, []);

  return {
    isSupported,
    isListening,
    interimText,
    error,
    start,
    stop,
    cancel,
  };
}
