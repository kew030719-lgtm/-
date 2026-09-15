export default function Toast({ message }: { message: string | null }) {
  return (
    <div id="toast" className={message ? 'toast show' : 'toast'} role="status" aria-live="polite">
      {message}
    </div>
  )
}