/**
 * Minimal MediaRecorder hook — records mic audio to a webm Blob.
 * Degrades to an error message when the mic is unavailable/denied.
 */

import { useCallback, useRef, useState } from 'react'

export interface VoiceRecorder {
  recording: boolean
  micError: string | null
  toggle: () => void
}

export function useVoiceRecorder(onAudio: (blob: Blob) => void): VoiceRecorder {
  const [recording, setRecording] = useState(false)
  const [micError, setMicError] = useState<string | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const onAudioRef = useRef(onAudio)
  onAudioRef.current = onAudio

  const start = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : undefined
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(chunksRef.current, { type: mimeType ?? 'audio/webm' })
        if (blob.size > 0) onAudioRef.current(blob)
      }
      recorder.start()
      recorderRef.current = recorder
      setMicError(null)
      setRecording(true)
    } catch {
      setMicError('Microphone unavailable — type your question instead.')
      setRecording(false)
    }
  }, [])

  const toggle = useCallback(() => {
    if (recording) {
      recorderRef.current?.stop()
      recorderRef.current = null
      setRecording(false)
    } else {
      void start()
    }
  }, [recording, start])

  return { recording, micError, toggle }
}
