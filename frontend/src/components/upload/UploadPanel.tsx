// =============================================================================
// UploadPanel — Drag-and-drop file upload
// =============================================================================
// Posts file to POST /api/v1/upload → returns { fileId } to parent.
// Real backend only. Shows clear error if backend is unreachable.
// =============================================================================

import React, { useCallback, useState, useRef } from 'react';
import { Upload, FileText, Loader2, CheckCircle, XCircle, Cloud } from 'lucide-react';
import { uploadFile } from '../../services/api';

interface UploadPanelProps {
  /** Called with fileId and fileName on successful upload */
  onJobStarted?: (fileId: string, fileName: string) => void;
  /** Whether the pipeline is currently processing */
  isProcessing?: boolean;
}

type UploadStatus = 'idle' | 'uploading' | 'success' | 'error';

export const UploadPanel: React.FC<UploadPanelProps> = ({ onJobStarted, isProcessing }) => {
  const [status, setStatus] = useState<UploadStatus>('idle');
  const [fileName, setFileName] = useState<string>('');
  const [error, setError] = useState<string>('');
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleUpload = useCallback(
    async (file: File) => {
      setFileName(file.name);
      setStatus('uploading');
      setError('');

      try {
        const response = await uploadFile(file);

        if (response.fileId) {
          setStatus('success');
          onJobStarted?.(response.fileId, file.name);
          return;
        }

        throw new Error('No fileId in response from server');
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.error('[UploadPanel] Upload failed:', message);
        setStatus('error');
        setError(message);
      }
    },
    [onJobStarted]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) handleUpload(file);
    },
    [handleUpload]
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleUpload(file);
    },
    [handleUpload]
  );

  const handleReset = useCallback(() => {
    setStatus('idle');
    setFileName('');
    setError('');
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, []);

  const isLocked = isProcessing || status === 'uploading';

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
      <h3 className="text-sm font-semibold text-slate-700 uppercase tracking-wide mb-4 flex items-center gap-2">
        <Upload className="w-4 h-4" />
        File Upload
      </h3>

      {/* Drop zone */}
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => !isLocked && fileInputRef.current?.click()}
        className={`
          border-2 border-dashed rounded-lg p-8 text-center transition-all duration-200
          ${isLocked ? 'cursor-wait' : 'cursor-pointer'}
          ${isDragOver
            ? 'border-blue-400 bg-blue-50'
            : status === 'success'
              ? 'border-green-300 bg-green-50'
              : status === 'error'
                ? 'border-red-300 bg-red-50'
                : 'border-slate-300 bg-slate-50 hover:border-blue-300 hover:bg-blue-50'
          }
        `}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.dxf,.dwg"
          onChange={handleFileSelect}
          className="hidden"
          disabled={isLocked}
        />

        {status === 'uploading' && (
          <div className="flex flex-col items-center gap-2">
            <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
            <p className="text-sm text-slate-600">Uploading {fileName}...</p>
            <p className="text-xs text-slate-400">Posting to ingestion service → S3</p>
          </div>
        )}

        {status === 'success' && (
          <div className="flex flex-col items-center gap-2" onClick={(e) => e.stopPropagation()}>
            <CheckCircle className="w-8 h-8 text-green-500" />
            <p className="text-sm text-green-700 font-medium">{fileName}</p>
            <p className="text-xs text-green-600">Uploaded — SSE pipeline started</p>
            {!isProcessing && (
              <button
                onClick={handleReset}
                className="mt-2 text-xs text-slate-500 hover:text-slate-700 underline"
              >
                Upload another file
              </button>
            )}
          </div>
        )}

        {status === 'error' && (
          <div className="flex flex-col items-center gap-2" onClick={(e) => e.stopPropagation()}>
            <XCircle className="w-8 h-8 text-red-500" />
            <p className="text-sm text-red-700 font-medium">{error}</p>
            <p className="text-xs text-red-400">Ensure the backend services are running</p>
            <button
              onClick={handleReset}
              className="mt-2 text-xs text-slate-500 hover:text-slate-700 underline"
            >
              Try again
            </button>
          </div>
        )}

        {status === 'idle' && (
          <div className="flex flex-col items-center gap-2">
            <FileText className="w-8 h-8 text-slate-400" />
            <p className="text-sm text-slate-600">
              Drag & drop <span className="font-medium">PDF / DXF / DWG</span> files here
            </p>
            <p className="text-xs text-slate-400">or click to browse</p>
          </div>
        )}
      </div>

      <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
        <span>
          Endpoint: <code className="bg-slate-100 px-1.5 py-0.5 rounded">POST /api/v1/upload</code>
        </span>
        <span className="flex items-center gap-1 text-green-500">
          <Cloud className="w-3 h-3" />
          Live
        </span>
      </div>
    </div>
  );
};

UploadPanel.displayName = 'UploadPanel';