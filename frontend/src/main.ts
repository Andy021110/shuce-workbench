import { createApp } from "vue"
import App from "./App.vue"
import "./style.css"
import { product } from './brand'

document.title = product.title
createApp(App).mount("#app")
