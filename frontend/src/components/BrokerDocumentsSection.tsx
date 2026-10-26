import { useEffect, useRef, useState } from 'react'
import { ExternalLink, FileText, Image, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  brokerDocumentUrl,
  deleteBrokerDocument,
  firstValidationMessage,
  listBrokerDocuments,
  uploadBrokerDocument,
  type BrokerDocument,
  type DocumentKind,
} from '@/data/api'

// The proof behind the compliance fields onboarding collects. An admin types a
// RIBO number and an E&O expiry; this is where the licence and the certificate
// themselves live.
//
// Shared by the onboarding success screen and the broker detail screen, for the
// same reason GeneratePasswordPanel is: two copies would drift into showing a
// document differently.
//
// THREE FIXED ROWS, always visible, whether or not anything is attached. The
// question this screen answers is "what is missing" at least as often as "what
// is here" — a list that renders only what exists cannot answer the first one.
//
// ALL OPTIONAL. Nothing here blocks onboarding, and an empty row is an ordinary
// state, not a warning.

const KINDS: { kind: DocumentKind; label: string; hint: string }[] = [
  { kind: 'photo', label: 'Photo', hint: 'JPG or PNG' },
  { kind: 'ribo_license', label: 'RIBO licence', hint: 'PDF, JPG or PNG' },
  { kind: 'eo_certificate', label: 'E&O certificate', hint: 'PDF, JPG or PNG' },
]

// Matches ALLOWED_TYPES in backend/schemas/broker_document.py. The backend is
// the authority — this only spares an admin picking a file that would be
// refused after uploading it.
const ACCEPT: Record<DocumentKind, string> = {
  photo: 'image/jpeg,image/png',
  ribo_license: 'application/pdf,image/jpeg,image/png',
  eo_certificate: 'application/pdf,image/jpeg,image/png',
}

export function BrokerDocumentsSection({ brokerId }: { brokerId: string }) {
  const [documents, setDocuments] = useState<BrokerDocument[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    listBrokerDocuments(brokerId)
      .then((rows) => {
        if (!cancelled) setDocuments(rows)
      })
      .catch(() => {
        // Not a toast. A broker with no documents and a failed fetch look the
        // same on screen, and an error banner on a section nobody was using is
        // noise. The rows still offer Upload, which is what recovers it.
        if (!cancelled) setDocuments([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [brokerId])

  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold">Documents</h3>
        <p className="text-muted-foreground text-xs">
          All optional. Uploading again replaces what is there.
        </p>
      </div>

      <div className="divide-y rounded-md border">
        {KINDS.map(({ kind, label, hint }) => (
          <DocumentRow
            key={kind}
            brokerId={brokerId}
            kind={kind}
            label={label}
            hint={hint}
            loading={loading}
            document={documents.find((d) => d.kind === kind)}
            onChange={(next) =>
              setDocuments((current) => [
                ...current.filter((d) => d.kind !== kind),
                ...(next ? [next] : []),
              ])
            }
          />
        ))}
      </div>
    </section>
  )
}

function DocumentRow({
  brokerId,
  kind,
  label,
  hint,
  loading,
  document: doc,
  onChange,
}: {
  brokerId: string
  kind: DocumentKind
  label: string
  hint: string
  loading: boolean
  document?: BrokerDocument
  onChange: (next: BrokerDocument | null) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)

  async function upload(file: File) {
    setBusy(true)
    try {
      onChange(await uploadBrokerDocument(brokerId, kind, file))
      toast.success(`${label} uploaded`)
    } catch (err) {
      toast.error(
        firstValidationMessage(err) ??
          `Could not upload the ${label.toLowerCase()}`,
      )
    } finally {
      setBusy(false)
      // Cleared so choosing the SAME file again still fires onChange — the
      // natural thing to do after a failed upload, and otherwise a dead click.
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  async function view() {
    try {
      // Opened from the signed URL rather than linked: the link expires in 15
      // minutes, so an <a href> rendered on load would be dead by the time
      // anyone clicked it.
      window.open(await brokerDocumentUrl(brokerId, kind), '_blank', 'noopener')
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not open that document')
    }
  }

  async function remove() {
    setBusy(true)
    try {
      await deleteBrokerDocument(brokerId, kind)
      onChange(null)
      toast.success(`${label} removed`)
    } catch (err) {
      toast.error(
        firstValidationMessage(err) ?? 'Could not remove that document',
      )
    } finally {
      setBusy(false)
    }
  }

  const Icon = kind === 'photo' ? Image : FileText

  return (
    <div className="flex items-center gap-3 px-3 py-2.5">
      <Icon className="text-muted-foreground size-4 shrink-0" />

      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{label}</div>
        <div className="text-muted-foreground truncate text-xs">
          {loading
            ? '…'
            : doc
              ? `${doc.file_name} · ${formatSize(doc.size_bytes)} · ${formatDate(doc.uploaded_at)}`
              : `Not uploaded · ${hint}`}
        </div>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT[kind]}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) upload(file)
        }}
      />

      <div className="flex shrink-0 items-center gap-1">
        {doc && (
          <Button variant="ghost" size="sm" onClick={view} disabled={busy}>
            <ExternalLink />
            View
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
        >
          <Upload />
          {busy ? 'Working…' : doc ? 'Replace' : 'Upload'}
        </Button>
        {doc && (
          <Button
            variant="ghost"
            size="sm"
            onClick={remove}
            disabled={busy}
            aria-label={`Remove ${label}`}
          >
            <Trash2 className="text-error" />
          </Button>
        )}
      </div>
    </div>
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-CA', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}
