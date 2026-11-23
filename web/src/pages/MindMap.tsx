import { useCallback, useEffect, useRef, useState, lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router-dom';
import { fetchJSON, postFormData, postJSON, extractBilibiliUrl } from '../api/client';
import { useTaskProgress } from '../hooks/useTaskProgress';
import ProgressBar from '../components/ProgressBar';
import { toast } from '../components/toastStore';
import type { TaskStatus } from '../types';
import {
  createMindMapSubmission,
  emptyMindMapSession,
  readMindMapSession,
  writeMindMapSession,
  type MindMapSession,
  type MindMapSubmission,
} from '../utils/mindmapTaskRecovery';
import { BrainCircuit, Play, Loader2, Square } from 'lucide-react';

const MarkmapView = lazy(() => import('../components/MarkmapView'));
type InputMode = 'text' | 'video';

async function postMindMapSubmission(submission: MindMapSubmission): Promise<string> {
  if (submission.kind === 'text') {
    const response = await postJSON<{ task_id: string }>('/api/text-to-mindmap', {
      content: submission.content,
      language: submission.language,
      request_id: submission.requestId,
    });
    return response.task_id;
  }
  const response = await postFormData<{ task_id: string }>('/api/video-to-mindmap', {
    url: submission.url,
    language: submission.language,
    request_id: submission.requestId,
  });
  return response.task_id;
}

export default function MindMap() {
  const [searchParams] = useSearchParams();
  const [session, setSession] = useState<MindMapSession>(
    () => readMindMapSession() || emptyMindMapSession(),
  );
  const sessionRef = useRef(session);
  const launchedRequestIds = useRef(new Set<string>());
  const autoLoadSourceIds = useRef(new Set<string>());

  const updateSession = useCallback((patch: Partial<MindMapSession>) => {
    const next = { ...sessionRef.current, ...patch };
    sessionRef.current = next;
    writeMindMapSession(next);
    setSession(next);
  }, []);

  const startSubmission = useCallback(async (submission: MindMapSubmission) => {
    launchedRequestIds.current.add(submission.requestId);
    updateSession({
      mode: submission.kind,
      url: submission.kind === 'video' ? submission.url : sessionRef.current.url,
      textContent: submission.kind === 'text' ? submission.content : sessionRef.current.textContent,
      taskId: null,
      pendingSubmission: submission,
      mindmapContent: '',
      status: 'processing',
      progress: 0,
      statusMessage: '正在提交任务...',
      error: '',
      cancelRequested: false,
    });
    try {
      const taskId = await postMindMapSubmission(submission);
      updateSession({ taskId, pendingSubmission: null, status: 'processing', statusMessage: '任务已提交' });
    } catch (error) {
      const message = error instanceof Error ? error.message : '提交结果暂不可确认';
      updateSession({
        error: `${message}。可用相同 request_id 重试确认任务，不会创建重复任务。`,
        status: null,
        statusMessage: '等待确认任务是否已创建',
      });
    }
  }, [updateSession]);

  const handleTaskUpdate = useCallback((task: TaskStatus) => {
    const wasTerminal = ['completed', 'error', 'cancelled'].includes(sessionRef.current.status || '');
    updateSession({
      status: task.status,
      progress: task.progress || 0,
      statusMessage: task.message || '',
      error: task.error || '',
      cancelRequested: task.cancel_requested === true,
      mindmapContent: task.status === 'completed' ? (task.mindmap || '') : sessionRef.current.mindmapContent,
    });
    if (task.status === 'completed' && task.mindmap && !wasTerminal) {
      toast('思维导图生成完成！', 'success');
    } else if (task.status === 'error' && !wasTerminal) {
      toast(task.error || '思维导图任务失败', 'error');
    } else if (task.status === 'cancelled' && !wasTerminal) {
      toast('后台任务已确认取消', 'info');
    }
  }, [updateSession]);

  const handleUnavailable = useCallback((error: Error) => {
    const status = (error as Error & { status?: number }).status;
    updateSession({
      status: 'error',
      error: status === 404
        ? '任务记录已失效或不存在。未自动重新生成，请手动提交新任务。'
        : `任务状态暂时无法读取：${error.message}`,
      statusMessage: '任务无法恢复',
      cancelRequested: false,
    });
  }, [updateSession]);

  useTaskProgress(session.taskId, handleTaskUpdate, handleUnavailable);

  // An unresolved submit may have reached the server even if its HTTP response was lost.
  // Replaying the same request_id lets the server return the original task instead of spawning a duplicate.
  useEffect(() => {
    const pending = session.pendingSubmission;
    if (!pending || session.taskId || launchedRequestIds.current.has(pending.requestId)) return;
    void startSubmission(pending);
  }, [session.pendingSubmission, session.taskId, startSubmission]);

  // History links may ask this page to build a mind map from a saved note. Record the
  // source task before loading it so navigation/remount does not silently launch it twice.
  useEffect(() => {
    const sourceTaskId = searchParams.get('taskId');
    const field = searchParams.get('field') || 'summary';
    if (!sourceTaskId || autoLoadSourceIds.current.has(sourceTaskId)) return;
    autoLoadSourceIds.current.add(sourceTaskId);
    const savedSession = sessionRef.current;
    const requestId = savedSession.autoSourceTaskId === sourceTaskId
      ? (savedSession.autoSourceRequestId || crypto.randomUUID())
      : crypto.randomUUID();
    updateSession({
      mode: 'text', autoSourceTaskId: sourceTaskId, autoSourceRequestId: requestId,
      status: null, error: '',
    });
    void fetchJSON<{ content: string }>(`/api/tasks/${encodeURIComponent(sourceTaskId)}/content?field=${encodeURIComponent(field)}`)
      .then(({ content }) => {
        const nextContent = content.trim();
        updateSession({ textContent: nextContent });
        if (nextContent) {
          void startSubmission({ kind: 'text', requestId, content: nextContent, language: 'zh' });
        }
      })
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : '读取历史笔记失败';
        updateSession({ status: 'error', error: message, statusMessage: '无法读取历史笔记' });
        toast(message, 'error');
      });
  }, [searchParams, startSubmission, updateSession]);

  const isWorking = session.status === 'queued' || session.status === 'processing';
  const hasPendingSubmit = Boolean(session.pendingSubmission && !session.taskId);

  const handleTextGenerate = () => {
    const content = session.textContent.trim();
    if (!content) return;
    void startSubmission(createMindMapSubmission('text', content));
  };

  const handleVideoGenerate = () => {
    const input = extractBilibiliUrl(session.url.trim());
    if (!input) return;
    void startSubmission(createMindMapSubmission('video', input));
  };

  const handleCancel = async () => {
    if (!session.taskId) return;
    try {
      const task = await fetchJSON<TaskStatus>(`/api/mindmap-tasks/${encodeURIComponent(session.taskId)}/cancel`, { method: 'POST' });
      handleTaskUpdate(task);
      if (task.cancel_requested) {
        toast('已发送取消请求，等待后台任务确认', 'info');
      }
    } catch (error) {
      toast(error instanceof Error ? error.message : '取消请求失败', 'error');
    }
  };

  const handleRetryPending = () => {
    if (session.pendingSubmission) void startSubmission(session.pendingSubmission);
  };

  return (
    <div className="flex h-full">
      <div className="w-96 border-r border-[var(--color-border)] bg-[var(--color-surface)] p-6 overflow-y-auto shrink-0">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-5">思维导图</h2>

        <div className="flex gap-1.5 mb-4">
          {([
            { key: 'text' as InputMode, label: '输入内容' },
            { key: 'video' as InputMode, label: '从视频生成' },
          ]).map((item) => (
            <button
              key={item.key}
              onClick={() => updateSession({ mode: item.key })}
              className={`px-3 py-1.5 text-xs rounded-md font-medium transition-colors ${
                session.mode === item.key
                  ? 'bg-[var(--color-accent)] text-white'
                  : 'bg-[var(--color-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--color-border-light)]'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="space-y-3">
          {session.mode === 'text' ? (
            <>
              <textarea
                value={session.textContent}
                onChange={(event) => updateSession({ textContent: event.target.value })}
                placeholder={'粘贴任意文本内容，AI 将自动提炼生成思维导图\n\n例如：课程笔记、文章摘要、会议纪要等'}
                rows={14}
                disabled={isWorking || hasPendingSubmit}
                className="w-full border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-accent)] focus:ring-1 focus:ring-[var(--color-accent)]/20 resize-none disabled:opacity-60"
              />
              <button
                onClick={hasPendingSubmit ? handleRetryPending : handleTextGenerate}
                disabled={isWorking || (!hasPendingSubmit && !session.textContent.trim())}
                className="w-full flex items-center justify-center gap-1.5 px-4 py-2.5 text-xs font-medium bg-[var(--color-accent)] text-white rounded-lg hover:bg-[var(--color-accent-hover)] disabled:opacity-40 transition-colors"
              >
                {isWorking ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                {hasPendingSubmit ? '用同一请求确认任务' : isWorking ? '生成中...' : session.status === 'error' ? '重新生成' : '生成思维导图'}
              </button>
              <p className="text-[11px] text-[var(--color-text-muted)] leading-relaxed">
                AI 会分析文本结构，自动提炼关键信息并生成可视化思维导图
              </p>
            </>
          ) : (
            <>
              <input
                value={session.url}
                onChange={(event) => updateSession({ url: event.target.value })}
                placeholder="粘贴视频链接或本地文件路径"
                className="w-full border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-accent)] focus:ring-1 focus:ring-[var(--color-accent)]/20"
                disabled={isWorking || hasPendingSubmit}
              />
              <button
                onClick={hasPendingSubmit ? handleRetryPending : handleVideoGenerate}
                disabled={isWorking || (!hasPendingSubmit && !session.url.trim())}
                className="w-full flex items-center justify-center gap-1.5 px-4 py-2.5 text-xs font-medium bg-[var(--color-accent)] text-white rounded-lg hover:bg-[var(--color-accent-hover)] disabled:opacity-40 transition-colors"
              >
                {isWorking ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                {hasPendingSubmit ? '用同一请求确认任务' : isWorking ? '生成中...' : session.status === 'error' ? '重新生成' : '生成'}
              </button>
              <p className="text-[11px] text-[var(--color-text-muted)] leading-relaxed">
                视频 → 转录 → 生成思维导图，支持在线链接和本地文件
              </p>
            </>
          )}
        </div>

        {(isWorking || hasPendingSubmit) && (
          <div className="mt-5 space-y-3">
            {session.taskId && (
              <button
                onClick={handleCancel}
                disabled={session.cancelRequested}
                className="w-full flex items-center justify-center gap-1.5 px-4 py-2 text-xs font-medium bg-red-50 text-red-600 border border-red-200 rounded-lg hover:bg-red-100 disabled:opacity-60 transition-colors"
              >
                <Square size={13} />
                {session.cancelRequested ? '取消请求已发送，等待后台确认' : '取消生成'}
              </button>
            )}
            {session.taskId && <ProgressBar progress={session.progress} />}
            {session.statusMessage && (
              <div className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
                {isWorking && !session.cancelRequested && <Loader2 size={12} className="animate-spin" />}
                {session.statusMessage}
              </div>
            )}
          </div>
        )}
        {session.error && (
          <p role="alert" className="mt-4 text-xs text-red-600 leading-relaxed">{session.error}</p>
        )}
        {session.status === 'cancelled' && (
          <p className="mt-4 text-xs text-[var(--color-text-secondary)]">任务已取消。模型服务或底层工作可能仍在收尾。</p>
        )}
        {session.status === 'error' && !session.error && (
          <p className="mt-4 text-xs text-red-600">任务失败，可手动重新生成。</p>
        )}
      </div>

      <div className="flex-1 overflow-hidden relative">
        {session.mindmapContent ? (
          <Suspense fallback={<p className="text-sm text-[var(--color-text-muted)] text-center py-12">加载思维导图...</p>}>
            <MarkmapView content={session.mindmapContent} className="absolute inset-0" />
          </Suspense>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-16 h-16 rounded-full bg-[var(--color-bg)] flex items-center justify-center mb-4">
              <BrainCircuit size={24} className="text-[var(--color-text-muted)]" />
            </div>
            <p className="text-sm text-[var(--color-text-secondary)] mb-1">
              {session.mode === 'text' ? '粘贴文本内容，AI 生成思维导图' : '输入视频链接或本地路径，自动生成思维导图'}
            </p>
            <p className="text-xs text-[var(--color-text-muted)]">
              {session.mode === 'text' ? '支持任意文本：笔记、文章、会议记录等' : '支持 YouTube、Bilibili 等平台，或本地视频文件'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
