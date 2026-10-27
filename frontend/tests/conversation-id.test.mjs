import test from 'node:test'
import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { createConversationId } from '../src/conversationId.ts'
test('HTTPS contexts can use the native UUID generator', () => {
 assert.equal(createConversationId({randomUUID:()=> 'native-id',getRandomValues:()=>{throw Error('unexpected fallback')}}),'native-id')
})
test('HTTP contexts without randomUUID can create distinct valid conversation IDs', () => {
 const source={getRandomValues:values=>webcrypto.getRandomValues(values)}
 const ids=Array.from({length:100},()=>createConversationId(source))
 assert.equal(new Set(ids).size,100)
 assert(ids.every(id=>/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id)))
})
