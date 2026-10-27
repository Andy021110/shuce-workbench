export function draftKey(userId: string | undefined, databaseId: string, conversationId: string) {
  return userId && conversationId ? `askdata-draft:${userId}:${databaseId}:${conversationId}` : ''
}

// Preset actions and resumed jobs never own the composer draft.
export function recoverSubmittedDraft(current: string, submitted?: string) {
  return current || submitted || ''
}
