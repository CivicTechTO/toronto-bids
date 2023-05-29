(ns frontend.details
	(:gen-class)
	(:require [clj-http.client :as client])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
	(:require [frontend.common :as common])
	(:require [frontend.calls :as calls])
)


(defn contents [api-base document_id]
	(let 
		[
			response (client/get (str api-base "details.json") {:query-params {"document_id" document_id} :accept :json})
			body (get response :body)
			call (json/read-str body)
		]
		(list (calls/call-lines call) [:div (get call "site_visit")] [:div (get call "description")])
	)
)

(defn details-body [api-base document_id]
	[:div#title "Details"]
	[:div (contents api-base document_id)]
)

(defn output [api-base local-base document_id]
	(page/html5 (list (common/head local-base "Details") [:body (details-body api-base document_id)]))
)


