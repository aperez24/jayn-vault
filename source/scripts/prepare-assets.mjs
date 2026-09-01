import { cp, mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'

const here = new URL('.', import.meta.url)
const sourceRoot = resolve(new URL('..', here).pathname)
const repoRoot = resolve(sourceRoot, '..')
const publicRoot = resolve(sourceRoot, 'public')
const publicAssets = resolve(publicRoot, 'assets')

await mkdir(publicAssets, { recursive: true })
await cp(resolve(repoRoot, 'assets/recovered.css'), resolve(publicAssets, 'recovered.css'))
await cp(resolve(repoRoot, 'jayn-emblem.png'), resolve(publicRoot, 'jayn-emblem.png'))
await cp(resolve(repoRoot, 'favicon.svg'), resolve(publicRoot, 'favicon.svg'))

console.log('Prepared recovered JAYN Vault visual assets.')
