import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './layout-fixes.css'
import './capacity.css'
import './dial-scaffold.css'
import './schedule-editor.css'
import './notification-center.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
