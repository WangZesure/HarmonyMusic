import router from '@ohos.router'
import http from '@ohos.net.http'
import util from '@ohos.util'
import promptAction from '@ohos.promptAction'
import { ConfigUtil as Config } from './common/ConfigUtil'
import preferencesUtil from './common/PreferencesUtil'
import { getSongDetail } from './common/Api'
import { ParamModel, ResponseModel } from './model/paramModel'
import { SongModel } from './model/AppModel'
import { AvPlayerUtils } from './utils/AvPlayerUtils'

// 一条对话消息；songs 为结果里的歌曲
interface AiSong {
  song_id: number
  name: string
  artist: string
  pic?: string
  url?: string
  reason?: string
  moods?: string[]
  scenes?: string[]
}
interface AiMsg {
  role: string          // 'user' | 'bot' | 'status'
  text: string
  songs?: AiSong[]
}

@Entry
@Component
struct AiSearch {
  @State msgs: AiMsg[] = []
  @State input: string = ''
  @State loading: boolean = false
  private sessionId: string = 'mobile-' + Date.now().toString()
  private scroller: Scroller = new Scroller()

  async addToken(header: Record<string, string>): Promise<Record<string, string>> {
    const token = await preferencesUtil.get('userinfoStore', 'token', '') as string
    if (token) { header['authorization'] = token }
    return header
  }

  // 结果歌曲回查数据库补 pic/url（契约 C7：工具只给 song_id）
  async hydrate(songs: AiSong[]): Promise<AiSong[]> {
    for (const s of songs) {
      const p = new ParamModel()
      p.id = s.song_id
      const res: ResponseModel = await getSongDetail(p)
      if (res.success && res.data) {
        const row = res.data[0] as Record<string, Object>
        s.pic = row['pic'] as string
        s.url = row['url'] as string
      }
    }
    return songs
  }

  // 用 SSE 调 Koa /agent/chat，逐步接收 正在理解 / 正在检索 / 结果
  async send() {
    const text = this.input.trim()
    if (!text || this.loading) { return }
    this.loading = true
    this.input = ''
    this.msgs.push({ role: 'user', text: text })
    const req = http.createHttp()
    const decoder = new util.TextDecoder('utf-8')
    let buf = ''
    req.on('dataReceive', (data: ArrayBuffer) => {
      buf += decoder.decodeWithStream(new Uint8Array(data), { stream: true })
      const blocks = buf.split('\n\n')
      buf = blocks.pop() as string
      for (const b of blocks) {
        const ev = (b.match(/event: (.+)/) || [])[1]
        const da = (b.match(/data: (.+)/) || [])[1]
        if (!da) { continue }
        const obj = JSON.parse(da) as Record<string, Object>
        if (ev === 'status') {
          this.msgs.push({ role: 'status', text: obj['text'] as string })
        } else if (ev === 'result') {
          const items = (obj['songs'] || []) as AiSong[]
          this.hydrate(items).then((songs: AiSong[]) => {
            this.msgs.push({ role: 'bot', text: (obj['message'] as string) || '为你找到：', songs: songs })
            this.loading = false
          })
        } else if (ev === 'error') {
          this.msgs.push({ role: 'bot', text: (obj['message'] as string) || '出错了' })
          this.loading = false
        }
      }
    })
    req.on('dataEnd', () => { this.loading = false })
    const header = await this.addToken({ 'Content-Type': 'application/json' })
    try {
      req.requestInStream(`${Config.SERVER}/agent/chat`, {
        method: http.RequestMethod.POST,
        header: header,
        extraData: JSON.stringify({ message: text, sessionId: this.sessionId }),
        connectTimeout: 15000,
        readTimeout: 60000
      })
    } catch (e) {
      this.msgs.push({ role: 'bot', text: '连接失败：' + JSON.stringify(e) })
      this.loading = false
    }
  }

  playSong(s: AiSong) {
    const song = new SongModel(s.song_id, s.name, s.pic || '', s.url || '')
    AvPlayerUtils.playSingle(song)
  }

  @Builder
  Bubble(m: AiMsg) {
    Column() {
      Text(m.text)
        .fontColor(m.role === 'user' ? Color.White : '#E8E4F2')
        .backgroundColor(m.role === 'user' ? '#A855F7' : (m.role === 'status' ? '#2A2140' : '#1C1826'))
        .padding('16lpx')
        .borderRadius('16lpx')
        .width(m.role === 'status' ? '70%' : 'auto')
      if (m.songs && m.songs.length > 0) {
        ForEach(m.songs, (s: AiSong) => {
          Row() {
            Image(Config.resolveUrl(s.pic || '')).width('80lpx').height('80lpx').borderRadius('10lpx')
              .margin({ right: '16lpx' })
            Column() {
              Text(s.name).fontSize('26lpx').fontWeight(FontWeight.Bold).fontColor(Color.White).maxLines(1)
              Text(s.artist).fontSize('22lpx').fontColor('#A8A3B8')
              if (s.reason) { Text(s.reason).fontSize('20lpx').fontColor('#8B7BA8').maxLines(2) }
            }.alignItems(HorizontalAlign.Start).layoutWeight(1)
          }
          .width('100%').padding('14lpx').backgroundColor('#241E33').borderRadius('12lpx').margin({ top: '10lpx' })
          .onClick(() => { this.playSong(s) })
        }, (s: AiSong) => s.song_id.toString())
      }
    }
    .width('100%')
    .alignItems(m.role === 'user' ? HorizontalAlign.End : HorizontalAlign.Start)
    .margin({ top: '14lpx' })
  }

  build() {
    Column() {
      // 顶栏
      Row() {
        Text('‹').fontSize('44lpx').fontColor(Color.White).onClick(() => { router.back() })
        Text('AI 搜歌').fontSize('34lpx').fontColor(Color.White).fontWeight(FontWeight.Bold).margin({ left: '10lpx' })
      }.width('100%').height('90lpx').padding({ left: '20lpx' }).backgroundColor('#17141F')
        .border({ width: { bottom: 1 }, color: '#2B2538' })

      // 对话区
      Scroll(this.scroller) {
        Column() {
          ForEach(this.msgs, (m: AiMsg) => { this.Bubble(m) }, (m: AiMsg, i: number) => i.toString())
          if (this.loading) { Text('…').fontColor('#8B7BA8').margin({ top: '14lpx' }) }
        }
        .width('100%')
        .padding('20lpx')
      }
      .layoutWeight(1)
      .edgeEffect(EdgeEffect.Spring)

      // 输入区
      Row() {
        TextInput({ text: this.input, placeholder: '说一句话找歌，如“深夜伤感的粤语歌”' })
          .layoutWeight(1).backgroundColor('#1C1826').fontColor(Color.White)
          .placeholderColor('#6E6880').borderRadius('24lpx')
          .onChange((v: string) => { this.input = v })
          .onSubmit(() => { this.send() })
        Button('发送').margin({ left: '12lpx' }).backgroundColor('#A855F7').onClick(() => { this.send() })
      }.width('100%').padding('16lpx').backgroundColor('#17141F')
    }
    .width('100%').height('100%').backgroundColor('#0E0C14')
  }
}
