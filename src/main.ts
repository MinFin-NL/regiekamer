import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
// Registers every <nldd-*> custom element (side-effectful, so import it whole).
import '@nldd/design-system'
import './assets/main.css'

createApp(App).use(createPinia()).mount('#app')
