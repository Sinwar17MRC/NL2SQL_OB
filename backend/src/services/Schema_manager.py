from sqlalchemy import create_engine, inspect, text 
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from typing import Dict, List, Any, Optional, Set, Tuple
from datetime import datetime
import re
from collections import defaultdict
from functools import lru_cache
from collections import Counter
import logging

from nltk.stem import WordNetLemmatizer
LEMMATIZER = WordNetLemmatizer()
NLTK_AVAILABLE = True

class SchemaManager:
    """
    A class to manage database connections and schema introspection.
    It is initialized with a db_url and provides separate methods
    to test the connection, get table names, and get the full detailed schema.
    """
    def __init__(self, db_url: str):
        try:
            self.db_url = db_url
            self.engine = create_engine(db_url)
            self.inspector = inspect(self.engine)
            self.logger = logging.getLogger(__name__)
    
            self.business_domains = self._load_business_domain_patterns()
            self.abbreviation_map = self._load_abbreviation_mappings()
            self.business_synonyms = self._load_business_synonyms()
        except Exception as e:
            raise ConnectionError(f"Failed to create SQLAlchemy engine. Check URL format. Error: {e}")

    def test_connection(self) -> bool:
        """
        Tests if a live connection can be made to the database.
        Returns True if successful, raises ConnectionError otherwise.
        """
        try:
            with self.engine.connect() as connection:
                print(f"SUCCESS: Connection to {self.engine.url.host} verified.")
                return True
        except SQLAlchemyError as e:
            print(f"ERROR: SQLAlchemy connection failed: {e}")
            raise ConnectionError(f"Database connection failed. Check URL/credentials. Error: {e}")

    def _get_table_row_count(self, schema_name: str, table_name: str) -> int:
        """Simple row counting function"""
        try:
           with self.engine.connect() as connection:
                query = text(f'SELECT COUNT(*) FROM [{schema_name}].[{table_name}]')
                result = connection.execute(query)
                row_count = result.fetchone()[0]
                return int(row_count) if row_count else 0
        except Exception as e:
            print(f"Row count failed for {schema_name}.{table_name}: {e}")
            return 0
    
    def get_schema_overview(self) -> Dict[str, Any]:
        """
        Get overview showing the TOP 2 SCHEMAS by total row count.
        Each schema shows its top 3 most dense tables.
        """
        try:
            schema_names = self.inspector.get_schema_names()
            schema_summaries = []
            
            print(f"Found schemas: {schema_names}")
            
            # Step 1: For each schema, get top 3 tables and calculate total rows
            for schema_name in schema_names:
                # Skip system schemas
                if schema_name.lower() in ['information_schema', 'sys', 'guest']:
                    print(f"Skipping system schema: {schema_name}")
                    continue
                    
                table_names = self.inspector.get_table_names(schema=schema_name)
                if not table_names:
                    print(f"Schema '{schema_name}' has no tables, skipping")
                    continue
                    
                schema_tables = []
                print(f"Processing schema '{schema_name}' with {len(table_names)} tables")
                
                # Get info for all tables in this schema
                for table_name in table_names:
                    try:
                        pk_constraint = self.inspector.get_pk_constraint(table_name, schema=schema_name)
                        primary_keys = pk_constraint.get('constrained_columns', []) if pk_constraint else None
                        column_count = len(self.inspector.get_columns(table_name, schema=schema_name))
                        row_count = self._get_table_row_count(schema_name, table_name)
                        
                        schema_tables.append({
                            "name": f"{schema_name}.{table_name}",
                            "schema": schema_name,
                            "table_name": table_name,
                            "primary_keys": primary_keys,
                            "column_count": column_count,
                            "row_count": row_count
                        })
                        
                    except Exception as e:
                        print(f"Warning: Could not process table '{schema_name}.{table_name}': {e}")
                        continue
                
                # Sort tables by row count and take top 3
                schema_tables.sort(key=lambda x: x['row_count'], reverse=True)
                top_3_tables = schema_tables[:3]
                
                # Calculate total row count for this schema's top 3 tables
                total_rows = sum(table['row_count'] for table in top_3_tables)
                
                schema_summaries.append({
                    "schema_name": schema_name,
                    "top_tables": top_3_tables,
                    "total_row_count": total_rows
                })
                
                print(f"Schema '{schema_name}': {len(top_3_tables)} tables, {total_rows:,} total rows")
                for table in top_3_tables:
                    print(f"  - {table['name']}: {table['row_count']:,} rows")
            
            if not schema_summaries:
                print("No schemas with processable tables found!")
                return {"tables": []}
            
            # Step 2: Sort schemas by total row count and take top 2
            schema_summaries.sort(key=lambda x: x['total_row_count'], reverse=True)
            
            print(f"Schema ranking by total row count:")
            for i, schema in enumerate(schema_summaries):
                print(f"  {i+1}. {schema['schema_name']}: {schema['total_row_count']:,} total rows")
            
            # Take top 2 schemas (or all if less than 2)
            top_schemas = schema_summaries[:2]
            print(f"Selected top {len(top_schemas)} schemas")
            
            # Step 3: Collect all tables from the selected schemas
            final_tables = []
            for schema_summary in top_schemas:
                final_tables.extend(schema_summary['top_tables'])
                print(f"Added {len(schema_summary['top_tables'])} tables from schema '{schema_summary['schema_name']}'")
            
            print(f"Overview: Returning {len(final_tables)} tables from {len(top_schemas)} schemas")
            return {"tables": final_tables}
            
        except SQLAlchemyError as e:
            raise ConnectionError(f"Failed to build schema overview. Error: {e}")
        
    def get_detailed_schema(self) -> Dict[str, Any]:
        """
        This method provides a detailed introspection of the entire database schema.
        This is the rich context needed for the AI agents.
        """
        try:
            all_tables = []
            schema_names = self.inspector.get_schema_names()
            
            for schema_name in schema_names:
                # Skip system schemas
                if schema_name.lower() in ['information_schema', 'sys', 'guest']:
                    continue
                    
                table_names = self.inspector.get_table_names(schema=schema_name)
                print(f"Getting detailed info for schema '{schema_name}' with {len(table_names)} tables")
                
                for table_name in table_names:
                    try:
                        # Get full column details
                        columns = []
                        for col in self.inspector.get_columns(table_name, schema=schema_name):
                            columns.append({"name": col['name'], "type": str(col['type'])})

                        # Get primary key
                        pk_constraint = self.inspector.get_pk_constraint(table_name, schema=schema_name)
                        primary_key = pk_constraint.get('constrained_columns', []) if pk_constraint else None

                        # Get foreign keys
                        fks = self.inspector.get_foreign_keys(table_name, schema=schema_name)
                        foreign_keys = [
                            {
                                "constrained_columns": fk['constrained_columns'],
                                "referred_table": fk['referred_table'],
                                "referred_columns": fk['referred_columns'],
                                "referred_schema": fk.get('referred_schema') or schema_name
                            } for fk in fks
                        ]

                        all_tables.append({
                            "name": f"{schema_name}.{table_name}",
                            "schema": schema_name,
                            "table_name": table_name,
                            "columns": columns,
                            "primary_key": primary_key if primary_key else None,
                            "foreign_keys": foreign_keys
                        })
                        
                    except Exception as e:
                        print(f"Warning: Could not process table '{schema_name}.{table_name}': {e}")
                        continue

            print(f"Detailed schema: Returning {len(all_tables)} tables from all schemas")
            return {"tables": all_tables}
            
        except SQLAlchemyError as e:
            raise ConnectionError(f"Failed to build detailed schema. Error: {e}")

    def dispose_engine(self):
        " cleaning up the engine connection's pool"
        if self.engine:
            self.engine.dispose()
            print("INFO: SQLAlchemy engine disposed.")
    
    def get_enhanced_schema_for_rag(self) -> Dict[str, Any]:
        """
        Enhanced version of get_detailed_schema with RAG-specific information.
        Builds on existing detailed schema extraction.
        """
        try:
            base_schema = self.get_detailed_schema()
            
            if not base_schema or not base_schema.get("tables"):
                return base_schema
            
            print(f"Enhancing schema with RAG-specific data for {len(base_schema['tables'])} tables")
            
            # Enhance each table with RAG-specific data
            enhanced_tables = []
            
            for table in base_schema["tables"]:
                try:
                    schema_name = table.get("schema", "")
                    table_name = table.get("table_name", "")
                    
                    print(f"Processing table: {schema_name}.{table_name}")
                    
                    # Add business context hints
                    table["business_hints"] = self._extract_business_hints(table)
                    
                    # Add sample data for context
                    table["sample_data"] = self._get_sample_data_safe(schema_name, table_name)
                    
                    # Add relationship analysis
                    analysis_result = self._analyze_table_relationships(table)
                    relationships = analysis_result["relationships"]
                    table["relationships_info"] = [
                        dict(list(rel.items())[:4])  
                        for rel in relationships
                        ]

                    # Add column analysis
                    table["column_analysis"] = self._analyze_column_patterns(table)
                    
                    # Add row count 
                    table["row_count"] = self._get_table_row_count(schema_name, table_name)
                    
                    enhanced_tables.append(table)
                    
                except Exception as e:
                    print(f"Warning: Could not enhance table {schema_name}.{table_name}: {e}")
                    # Add the table without enhancements
                    enhanced_tables.append(table)
            
            # Create enhanced schema structure
            enhanced_schema = {
                "tables": enhanced_tables,
                "enhanced_at": datetime.now().isoformat(),
                "enhancement_type": "rag_focused"
            }
            
            print(f"Schema enhancement completed successfully")
            return enhanced_schema
            
        except Exception as e:
            print(f"Error enhancing schema for RAG: {str(e)}")
            # Return base schema as fallback
            return self.get_detailed_schema()
    
    def _load_business_domain_patterns(self):
        return {
            "customer_management": {
                "patterns": [
                    "customer", "client", "user", "account", "member", "person", 
                    "contact", "subscriber", "buyer", "consumer",
                    "client", "utilisateur", "compte", "membre", "personne", 
                    "contact", "abonne", "acheteur", "consommateur"
                ],
                "strength_weight": 1.0,
                "clustering_priority": "high"
            },
            "order_processing": {
                "patterns": [
                    "order", "purchase", "transaction", "sale", "invoice", 
                    "receipt", "payment", "checkout", "cart", "basket",
                    "commande", "achat", "transaction", "vente", "facture",
                    "recu", "paiement", "panier", "caisse"
                ],
                "strength_weight": 1.0,
                "clustering_priority": "high"
            },
            "inventory_management": {
                "patterns": [
                    "product", "item", "inventory", "stock", "catalog", 
                    "merchandise", "goods", "sku", "variant", "asset",
                    "produit", "article", "inventaire", "stock", "catalogue",
                    "marchandise", "bien", "variante", "actif"
                ],
                "strength_weight": 0.9,
                "clustering_priority": "high"
            },
            "financial_data": {
                "patterns": [
                    "payment", "billing", "invoice", "finance", "accounting", 
                    "revenue", "expense", "budget", "cost", "price",
                    "paiement", "facturation", "facture", "finance", "comptabilite",
                    "revenu", "depense", "budget", "cout", "prix"
                ],
                "strength_weight": 0.8,
                "clustering_priority": "medium"
            },
            "hr_management": {
                "patterns": [
                    "employee", "staff", "department", "role", "position", 
                    "salary", "payroll", "benefit", "leave", "attendance",
                    "employe", "personnel", "departement", "role", "poste",
                    "salaire", "paie", "avantage", "conge", "presence"
                ],
                "strength_weight": 0.7,
                "clustering_priority": "medium"
            },
            "logistics": {
                "patterns": [
                    "shipping", "delivery", "warehouse", "supplier", "vendor", 
                    "transport", "freight", "shipment", "location", "address",
                    "livraison", "entrepot", "fournisseur", "vendeur",
                    "transport", "fret", "expedition", "localisation", "adresse"
                ],
                "strength_weight": 0.8,
                "clustering_priority": "medium"
            },
            "content_management": {
                "patterns": [
                    "content", "article", "document", "media", "file", 
                    "page", "post", "comment", "message", "notification",
                    "contenu", "article", "document", "media", "fichier",
                    "page", "publication", "commentaire", "message", "notification"
                ],
                "strength_weight": 0.6,
                "clustering_priority": "low"
            },
            "audit_tracking": {
                "patterns": [
                    "log", "audit", "history", "tracking", "event", 
                    "activity", "session", "trace", "monitor",
                    "journal", "audit", "historique", "suivi", "evenement",
                    "activite", "session", "trace", "surveillance"
                ],
                "strength_weight": 0.3,
                "clustering_priority": "low"
            }
        }
    
    def _load_abbreviation_mappings(self) -> Dict[str, str]:
        """Load bilingual (EN/FR) common database abbreviations and their expansions."""
        return {
            # --- English ---
            'addr': 'address', 'qty': 'quantity', 'amt': 'amount', 'desc': 'description',
            'mgr': 'manager', 'dept': 'department', 'emp': 'employee', 'cust': 'customer',
            'ord': 'order', 'prod': 'product', 'inv': 'invoice', 'pymnt': 'payment',
            'acct': 'account', 'usr': 'user', 'pwd': 'password', 'auth': 'authentication',
            'cfg': 'configuration', 'pref': 'preference', 'cat': 'category',
            'img': 'image', 'doc': 'document', 'msg': 'message', 'notif': 'notification',
            'sess': 'session', 'req': 'request', 'resp': 'response', 'stat': 'status',
            'temp': 'temporary', 'del': 'delete', 'upd': 'update', 'ins': 'insert',
            'ref': 'reference', 'rel': 'relationship', 'assoc': 'association',
            'num': 'number', 'cnt': 'count', 'max': 'maximum', 'min': 'minimum',
            'avg': 'average', 'tot': 'total', 'disc': 'discount', 'pct': 'percentage',
            'dt': 'date', 'tm': 'time', 'ts': 'timestamp', 'yr': 'year', 'mo': 'month',
            'id': 'identifier', 'pk': 'primary_key', 'fk': 'foreign_key', 'idx': 'index',

            # --- French (ASCII, no accents) ---
            'adr': 'adresse', 'qte': 'quantite', 'mnt': 'montant', 'descr': 'description',
            'gest': 'gestionnaire', 'dept': 'departement', 'emp': 'employe', 'cli': 'client',
            'cmd': 'commande', 'prod': 'produit', 'fact': 'facture', 'paie': 'paiement',
            'cpt': 'compte', 'util': 'utilisateur', 'mdp': 'motdepasse', 'auth': 'authentification',
            'cfg': 'configuration', 'pref': 'preference', 'cat': 'categorie',
            'img': 'image', 'doc': 'document', 'msg': 'message', 'notif': 'notification',
            'sess': 'session', 'req': 'requete', 'resp': 'reponse', 'statut': 'statut',
            'temp': 'temporaire', 'suppr': 'supprimer', 'maj': 'miseajour', 'ins': 'inserer',
            'ref': 'reference', 'rel': 'relation', 'assoc': 'association',
            'nb': 'nombre', 'cnt': 'compter', 'max': 'maximum', 'min': 'minimum',
            'moy': 'moyenne', 'tot': 'total', 'rem': 'remise', 'pct': 'pourcentage',
            'dt': 'date', 'hr': 'heure', 'ts': 'horodatage', 'an': 'annee', 'mois': 'mois',
            'id': 'identifiant', 'pk': 'cleprimaire', 'fk': 'cleetrangere', 'idx': 'index'
        }

    def _load_business_synonyms(self) -> Dict[str, Set[str]]:
        """Load enriched bilingual (EN/FR) business domain synonyms for semantic matching."""
        return {
            # --- Customers / Users ---
            'customer': {'client', 'user', 'account', 'member', 'buyer', 'consumer',
                        'subscriber', 'participant', 'shopper', 'patron', 'prospect',
                        'utilisateur', 'compte', 'membre', 'acheteur', 'consommateur',
                        'abonne', 'participant'},
            
            # --- Products ---
            'product': {'item', 'merchandise', 'good', 'sku', 'article', 'commodity',
                        'offering', 'service', 'deliverable', 'asset',
                        'produit', 'article', 'marchandise', 'bien', 'stock',
                        'service', 'ressource'},
            
            # --- Orders ---
            'order': {'purchase', 'transaction', 'sale', 'requisition', 'request',
                    'booking', 'reservation', 'command', 'commande', 'achat',
                    'transaction', 'vente', 'demande', 'requete', 'reservation'},
            
            # --- Payments ---
            'payment': {'billing', 'invoice', 'charge', 'fee', 'cost', 'expense',
                        'settlement', 'remittance', 'compensation', 'payout',
                        'paiement', 'facture', 'frais', 'cout', 'depense',
                        'reglement', 'remboursement'},
            
            # --- Employees ---
            'employee': {'staff', 'worker', 'personnel', 'team_member', 'associate',
                        'agent', 'operator', 'crew', 'representative',
                        'employe', 'personnel', 'salarié', 'travailleur', 'agent',
                        'collaborateur', 'representant'},
            
            # --- Departments ---
            'department': {'division', 'unit', 'section', 'branch', 'group',
                        'bureau', 'service', 'departement', 'unite', 'branche',
                        'division', 'cellule', 'groupe'},
            
            # --- Managers ---
            'manager': {'supervisor', 'director', 'lead', 'head', 'chief', 'boss',
                        'administrator', 'coordinator', 'responsable', 'gestionnaire',
                        'superviseur', 'directeur', 'chef', 'administrateur'},
            
            # --- Categories ---
            'category': {'type', 'kind', 'class', 'group', 'classification',
                        'segment', 'family', 'categorie', 'type', 'classe',
                        'groupe', 'classification', 'famille', 'segment'},
            
            # --- Status ---
            'status': {'state', 'condition', 'stage', 'phase', 'level', 'situation',
                    'progress', 'statut', 'etat', 'situation', 'phase', 'niveau',
                    'progression'},
            
            # --- Quantities ---
            'quantity': {'amount', 'count', 'number', 'volume', 'measure', 'total',
                        'qte', 'quantite', 'nombre', 'volume', 'mesure', 'total'},
            
            # --- Prices ---
            'price': {'cost', 'rate', 'fee', 'charge', 'value', 'amount',
                    'tariff', 'fare', 'pricing',
                    'prix', 'cout', 'valeur', 'montant', 'tarif'},
            
            # --- Names ---
            'name': {'title', 'label', 'identifier', 'designation', 'term', 'caption',
                    'nom', 'intitule', 'libelle', 'designation', 'identifiant'},
            
            # --- Dates / Time ---
            'date': {'day', 'dt', 'jour', 'date', 'calendrier'},
            'time': {'hour', 'minute', 'second', 'clock', 'horaire', 'temps', 'heure'},
            'timestamp': {'ts', 'datetime', 'horodatage', 'dateheure'},
            
            # --- IDs / Keys ---
            'id': {'identifier', 'pk', 'key', 'fk', 'cle', 'identifiant', 'cleetrangere'},
            
            # --- Misc Business Terms ---
            'document': {'file', 'record', 'doc', 'rapport', 'fichier'},
            'message': {'msg', 'notification', 'communication', 'mail', 'courriel'},
            'account': {'profile', 'record', 'compte', 'profil', 'dossier'}
        }
    
    @lru_cache(maxsize=2000)
    def get_enhanced_tokens(self, text: str) -> frozenset[str]:
        """
        Advanced tokenization with lemmatization (return to original form{running->run}), abbreviation expansion, and synonym enriching.
        """
        if not text:
            return frozenset()
        
        # Step 1: Basic normalization
        normalized = re.sub(r'[_.-]', ' ', text)
        normalized = re.sub(r'(?<!^)(?=[A-Z])', ' ', normalized)
        
        # Step 2: Extract base tokens
        base_tokens = set()
        for word in normalized.lower().split():
            if word:
                expanded_word = self.abbreviation_map.get(word, word)
                base_tokens.add(expanded_word)
        
        # Step 3: Lemmatization (if available)
        if NLTK_AVAILABLE:
            lemmatized_tokens = set()
            for token in base_tokens:
                try:
                    lemmatized = LEMMATIZER.lemmatize(token)
                    lemmatized_tokens.add(lemmatized)
                except:
                    lemmatized_tokens.add(token)
            base_tokens = lemmatized_tokens
        
        # Step 4: Add business synonyms
        enhanced_tokens = base_tokens.copy()
        for token in base_tokens:
            if token in self.business_synonyms:
                enhanced_tokens.update(self.business_synonyms[token])
        
        return frozenset(enhanced_tokens)

    def advanced_pattern_matching(self, text: str, patterns: List[str], 
                                threshold: float = 0.3) -> Tuple[bool, float, List[str]]:
        """
        Advanced pattern matching with confidence scoring, business patterns comparison against the augmented text
        Returns: (matches, confidence_score, matched_patterns)
        """
        if not text or not patterns:
            return False, 0.0, []
        
        text_tokens = self.get_enhanced_tokens(text)
        matched_patterns = []
        total_score = 0.0
        
        for pattern in patterns:
            pattern_tokens = self.get_enhanced_tokens(pattern)
            
            if not pattern_tokens:
                continue
            
            intersection = text_tokens.intersection(pattern_tokens)
            if intersection:
                pattern_score = len(intersection) / len(pattern_tokens)
                total_score += pattern_score
                matched_patterns.append({
                    'pattern': pattern,
                    'score': pattern_score,
                    'matched_tokens': list(intersection)
                })
        
        if matched_patterns:
            confidence = total_score / len(patterns)
            matches = confidence >= threshold
            return matches, confidence, matched_patterns
        
        return False, 0.0, []

    def _extract_business_hints(self, table_info: Dict[str, Any]) -> List[str]:
        """Enhanced business intelligence extraction with confidence scoring."""
        table_name = table_info.get("table_name", "")
        columns = table_info.get("columns", [])
        foreign_keys = table_info.get("foreign_keys", [])
        
        hints = []
        domain_analysis = {}
        
        # Advanced domain classification
        for domain_name, domain_config in self.business_domains.items():
            patterns = domain_config["patterns"] 
            
            # Check table name
            table_matches, table_confidence, _ = self.advanced_pattern_matching(
                table_name, patterns, threshold=0.2
            )
            
            # Check column names
            column_text = " ".join([col["name"] for col in columns])
            column_matches, column_confidence, _ = self.advanced_pattern_matching(
                column_text, patterns, threshold=0.1
            )
            
            # Combined scoring
            combined_confidence = (table_confidence * 0.7 + column_confidence * 0.3) * domain_config["strength_weight"]
            
            if combined_confidence > 0.3:
                hints.append(domain_name)
        
        # Add specialized pattern detection
        specialized_hints = self._detect_specialized_patterns(table_info)
        hints.extend(specialized_hints)
        
        return list(set(hints))  # Remove duplicates

    def _detect_specialized_patterns(self, table_info: Dict[str, Any]) -> List[str]:
        """Detect specialized database patterns."""
        patterns = []
        columns = table_info.get("columns", [])
        foreign_keys = table_info.get("foreign_keys", [])
        
        # Junction table detection
        if len(foreign_keys) >= 2 and len(columns) <= 6:
            patterns.append("junction_table")
        
        # Lookup table detection
        name_columns = [col for col in columns if 
                    any(token in self.get_enhanced_tokens(col["name"]) 
                        for token in ["name", "label", "description", "title"])]
        
        if len(columns) <= 5 and name_columns and len(foreign_keys) == 0:
            patterns.append("lookup_table")
        
        # Audit table detection
        all_column_tokens = set()
        for col in columns:
            all_column_tokens.update(self.get_enhanced_tokens(col["name"]))
        
        # 8. Audit table detection
        audit_indicators = {
            "created", "updated", "modified", "deleted", "user", "action", "timestamp",
            "cree", "modifie", "supprime", "utilisateur", "action", "horodatage"  
        }
        if audit_indicators.intersection(all_column_tokens):
            patterns.append("audit_table")
        
        # 1. Contact information detection
        contact_indicators = {
            "email", "phone", "mobile", "fax", "contact", "address",
            "courriel", "telephone", "mobile", "fax", "contact", "adresse"  
        }
        if contact_indicators.intersection(all_column_tokens):
            patterns.append("contains_contact_info")
        
        # 2. Financial data detection
        financial_indicators = {
            "amount", "price", "cost", "total", "subtotal", "tax", "discount", "fee",
            "montant", "prix", "cout", "total", "soustotal", "taxe", "remise", "frais"  
        }
        if financial_indicators.intersection(all_column_tokens):
            patterns.append("financial_transactions")
        
        # 3. Temporal data detection
        temporal_indicators = {
            "date", "time", "created", "updated", "modified", "timestamp", "when",
            "date", "heure", "cree", "modifie", "mis_a_jour", "horodatage", "quand"  
        }
        if temporal_indicators.intersection(all_column_tokens):
            patterns.append("time_sensitive_data")
        
        # 4. Status/workflow detection
        status_indicators = {
            "status", "state", "stage", "phase", "active", "enabled", "approved",
            "statut", "etat", "etape", "phase", "actif", "active", "approuve" 
        }
        if status_indicators.intersection(all_column_tokens):
            patterns.append("workflow_tracking")
        
        # 5. Geographic data detection
        geo_indicators = {
            "address", "city", "state", "country", "zip", "postal", "location", "region",
            "adresse", "ville", "etat", "pays", "code_postal", "localisation", "region"  
        }
        if geo_indicators.intersection(all_column_tokens):
            patterns.append("geographic_data")
        
        
        return patterns        
    
    def _get_sample_data_safe(self, schema_name: str, table_name: str, max_rows: int = 5) -> List[Dict[str, Any]]:
        """
        Get representative sample data with privacy protection.
        Enhanced version of your existing row counting approach.
        """
        try:
            with self.engine.connect() as connection:
                # Build sample query with TABLESAMPLE for better sampling on large tables
                # Note: TABLESAMPLE works on SQL Server, for other DBs would need different approach
                
                # First check if table has data
                count_query = text(f'SELECT COUNT(*) FROM [{schema_name}].[{table_name}]')
                count_result = connection.execute(count_query)
                row_count = count_result.fetchone()[0]
                
                if row_count == 0:
                    return []
                
                # Choose sampling strategy based on table size
                if row_count <= 100:
                    # Small table - just get top rows
                    sample_query = text(f'SELECT TOP {max_rows} * FROM [{schema_name}].[{table_name}]')
                else:
                    # Large table - try to get more representative sample
                    # Use TABLESAMPLE for better distribution (SQL Server specific)
                    try:
                        sample_query = text(f'''
                            SELECT TOP {max_rows} * 
                            FROM [{schema_name}].[{table_name}] TABLESAMPLE (500 ROWS)
                        ''')
                    except:
                        # Fallback to simple TOP if TABLESAMPLE fails
                        sample_query = text(f'SELECT TOP {max_rows} * FROM [{schema_name}].[{table_name}]')
                
                result = connection.execute(sample_query)
                
                # Get column names
                columns = [desc.name for desc in result.description]
                
                # Fetch sample rows
                rows = result.fetchall()
                
                # Convert to list of dictionaries with privacy protection
                sample_data = []
                for row in rows:
                    row_dict = {}
                    for i, value in enumerate(row):
                        column_name = columns[i].lower()
                        
                        # Privacy protection - mask sensitive columns
                        if self._is_sensitive_column(column_name):
                            row_dict[columns[i]] = "[SENSITIVE_DATA]"
                        elif value is not None:
                            # Truncate long values
                            str_value = str(value)
                            if len(str_value) > 50:
                                row_dict[columns[i]] = str_value[:50] + "..."
                            else:
                                row_dict[columns[i]] = str_value
                        else:
                            row_dict[columns[i]] = None
                    
                    sample_data.append(row_dict)
                
                return sample_data
                
        except Exception as e:
            print(f"Could not get sample data for {schema_name}.{table_name}: {e}")
            return []
    
    def _is_sensitive_column(self, column_name: str) -> bool:# Done!
        """Enhanced sensitive column detection using token analysis."""
        col_tokens = self.get_enhanced_tokens(column_name)
        
        sensitive_patterns = {
            # English patterns
                "password", "pwd", "secret", "token", "key", "hash",
                "ssn", "social", "credit", "card", "account_number",
                "email", "phone", "mobile", "address", "street",
                "salary", "wage", "income", "bank", "routing",
                # French patterns
                "mot_de_passe", "secret", "jeton", "cle", "hachage",
                "nas", "credit", "carte", "numero_compte",
                "courriel", "telephone", "mobile", "adresse", "rue",
                "salaire", "revenu", "banque"
        }
        
        return bool(col_tokens.intersection(sensitive_patterns))
        
    def _analyze_table_relationships(self, table_info: Dict[str, Any]) -> Dict[str, Any]:
        """Enhanced relationship analysis using smart tokenization."""
        foreign_keys = table_info.get("foreign_keys", [])
        
        relationships = []
        
        # Relationship patterns (same as your current ones)
        business_patterns = [
            "customer", "client", "user", "product", "order", "invoice", "payment", "account", "member",
            "client", "utilisateur", "produit", "commande", "facture", "paiement", "compte", "membre"
        ]
        
        audit_patterns = [
            "created_by", "updated_by", "modified_by", "deleted_by", "approved_by", "assigned_by",
            "cree_par", "modifie_par", "supprime_par", "approuve_par", "assigne_par"
        ]
        
        lookup_patterns = [
            "status", "type", "category", "kind", "class", "level", "priority", "state",
            "statut", "type", "categorie", "genre", "classe", "niveau", "priorite", "etat"
        ]
        
        hierarchical_patterns = [
            "parent", "manager", "supervisor", "head", "owner", "lead",
            "parent", "gestionnaire", "superviseur", "chef", "proprietaire", "responsable"
        ]
        
        # ENHANCEMENT: Convert patterns to token sets for smart matching
        business_tokens = set()
        for pattern in business_patterns:
            business_tokens.update(self.get_enhanced_tokens(pattern))
        
        audit_tokens = set()
        for pattern in audit_patterns:
            audit_tokens.update(self.get_enhanced_tokens(pattern))
        
        lookup_tokens = set()
        for pattern in lookup_patterns:
            lookup_tokens.update(self.get_enhanced_tokens(pattern))
        
        hierarchical_tokens = set()
        for pattern in hierarchical_patterns:
            hierarchical_tokens.update(self.get_enhanced_tokens(pattern))
        
        for fk in foreign_keys:
            referred_table = fk.get("referred_table", "")
            referred_schema = fk.get("referred_schema", table_info.get("schema", ""))
            constrained_columns = fk.get("constrained_columns", [])
            
            if not constrained_columns:
                continue
            
            for column_name in constrained_columns:
                # ENHANCEMENT: Use smart tokenization instead of simple string matching
                col_tokens = self.get_enhanced_tokens(column_name)
                referred_table_tokens = self.get_enhanced_tokens(referred_table)
                
                # Initialize relationship classification
                relationship_type = "general_reference"
                strength_score = 0.5
                clustering_weight = "medium"
                
                # Enhanced pattern matching using token intersection
                if col_tokens.intersection(business_tokens):
                    relationship_type = "core_business"
                    strength_score = 1.0
                    clustering_weight = "high"
                
                elif col_tokens.intersection(audit_tokens):
                    relationship_type = "audit"
                    strength_score = 0.2
                    clustering_weight = "low"
                
                elif col_tokens.intersection(lookup_tokens):
                    relationship_type = "lookup"
                    strength_score = 0.4
                    clustering_weight = "medium"
                
                elif col_tokens.intersection(hierarchical_tokens):
                    relationship_type = "hierarchical"
                    strength_score = 0.7
                    clustering_weight = "medium"
                
                # Enhanced ID pattern analysis
                elif col_tokens.intersection({"id", "identifier", "key"}):
                    if referred_table_tokens.intersection(business_tokens):
                        relationship_type = "core_business"
                        strength_score = 0.9
                        clustering_weight = "high"
                    elif referred_table_tokens.intersection(lookup_tokens):
                        relationship_type = "lookup"
                        strength_score = 0.4
                        clustering_weight = "medium"
                    elif referred_table_tokens.intersection(audit_tokens):
                        relationship_type = "audit"
                        strength_score = 0.3
                        clustering_weight = "low"
                    else:
                        relationship_type = "lookup_reference"
                        strength_score = 0.5
                        clustering_weight = "medium"
                
                elif col_tokens.intersection({"key", "business", "external", "reference"}):
                    relationship_type = "business_key"
                    strength_score = 0.8
                    clustering_weight = "high"
                
                # Create relationship record
                relationship = {
                    "target_table": referred_table,
                    "target_schema": referred_schema,
                    "via_column": column_name,
                    "relationship_type": relationship_type,
                    "strength_score": strength_score,
                    "clustering_weight": clustering_weight,
                    "full_target_id": f"{referred_schema}.{referred_table}",
                    "is_cross_schema": referred_schema != table_info.get("schema", "")
                }
                
                relationships.append(relationship)
        
        # Calculate summary statistics (same as your original)
        total_relationships = len(relationships)
        high_strength_rels = [rel for rel in relationships if rel["clustering_weight"] == "high"]
        strongest_targets = list(set([rel["full_target_id"] for rel in high_strength_rels]))
        
        referenced_schemas = set(rel["target_schema"] for rel in relationships)
        cross_schema_rels = [rel for rel in relationships if rel["is_cross_schema"]]
        has_cross_schema = len(cross_schema_rels) > 0
        
        return {
            "relationships": relationships,
            "summary": {
                "total_relationships": total_relationships,
                "cross_schema_relationships": has_cross_schema,
                "referenced_schemas": list(referenced_schemas)
            },
            "clustering_guidance": {
                "strongest_targets": strongest_targets,
                "avoid_clustering_with": [rel["full_target_id"] for rel in relationships if rel["clustering_weight"] == "low"]
            }
        }
    
    def _analyze_column_patterns(self, table_info: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze column patterns for better business understanding."""
        columns = table_info.get("columns", [])
        primary_key = table_info.get("primary_key", [])
        
        # Initialize analysis categories
        identity_columns = []
        descriptive_columns = []
        temporal_columns = []
        financial_columns = []
        status_columns = []
        sensitive_columns = []
        
        # Analyze each column (bilingual patterns)
        for col in columns:
            col_name = col.get("name", "").lower()
            col_type = col.get("type", "").lower()

             # Check if column is sensitive (for RAG awareness)
            if self._is_sensitive_column(col_name):
                sensitive_columns.append(col["name"])
            
            # Classify by naming patterns
            if col_name in [pk.lower() for pk in primary_key] or "id" in col_name:
                identity_columns.append(col["name"])
            
            # Descriptive columns (bilingual)
            if any(pattern in col_name for pattern in ["name", "title", "description", "label", "nom", "titre", "libelle"]):
                descriptive_columns.append(col["name"])
            
            # Temporal columns (bilingual)
            if any(pattern in col_name for pattern in ["date", "time", "created", "updated", "modified", "cree", "modifie", "heure"]) or \
               any(pattern in col_type for pattern in ["date", "time", "timestamp"]):
                temporal_columns.append(col["name"])
            
            # Financial columns (bilingual)
            if any(pattern in col_name for pattern in ["amount", "price", "cost", "total", "fee", "salary", "montant", "prix", "cout", "frais", "salaire"]) or \
               any(pattern in col_type for pattern in ["money", "decimal", "numeric"]):
                financial_columns.append(col["name"])
            
            # Status columns (bilingual)
            if any(pattern in col_name for pattern in ["status", "state", "active", "enabled", "flag", "statut", "etat", "actif"]):
                status_columns.append(col["name"])
        
        # Calculate column statistics
        total_columns = len(columns)
        
        return {
            "total_columns": total_columns,
            "identity_columns": identity_columns,
            "descriptive_columns": descriptive_columns,
            "temporal_columns": temporal_columns,
            "financial_columns": financial_columns,
            "status_columns": status_columns,
            "sensitive_columns": sensitive_columns
        }
    
    def _calculate_clustering_metadata(self, enhanced_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate overall schema statistics for RAG optimization."""
        if not enhanced_tables:
            return {}
        
        # Gather statistics
        total_tables = len(enhanced_tables)
        total_relationships = sum(len(table.get("foreign_keys", [])) for table in enhanced_tables)
        
        # Business domain analysis
        all_business_hints = []
        tables_with_relationships = 0
        
        for table in enhanced_tables:
            all_business_hints.extend(table.get("business_hints", []))
            if len(table.get("foreign_keys", [])) > 0:
              tables_with_relationships += 1
        
        # Count unique business domains
        unique_domains = list(set(all_business_hints))
        
        # Calculate clustering potential
        relationship_ratio = tables_with_relationships / total_tables if total_tables > 0 else 0
        avg_relationships_per_table = total_relationships / total_tables if total_tables > 0 else 0
        
        if relationship_ratio > 0.7 and avg_relationships_per_table > 1.0:
            clustering_potential = "high"
        elif relationship_ratio > 0.4 and avg_relationships_per_table > 0.5:
            clustering_potential = "medium"
        else:
            clustering_potential = "low"
        
        return {
            "total_tables": total_tables,
            "total_relationships": total_relationships,
            "tables_with_relationships": tables_with_relationships,
            "business_domains_detected": unique_domains,
            "clustering_potential": clustering_potential
        }
    
    def get_tables_for_clustering(self) -> List[Dict[str, Any]]:
        """Get enhanced table information formatted for clustering analysis."""
        try:
            enhanced_schema = self.get_enhanced_schema_for_rag()
            
            if not enhanced_schema or not enhanced_schema.get("tables"):
                print("No enhanced schema data available for clustering")
                return []
            
            # Transform enhanced tables into clustering format
            clustering_tables = []
            
            for table in enhanced_schema["tables"]:
                full_relationship_metrics = self._analyze_table_relationships(table)
                clustering_table = {
                    "schema_name": table.get("schema", ""),
                    "table_name": table.get("table_name", ""),
                    "full_table_id": f"{table.get('schema', '')}.{table.get('table_name', '')}",
                    "columns": table.get("columns", []),
                    "foreign_keys": table.get("foreign_keys", []),
                    "primary_key": table.get("primary_key", []),
                    "business_hints": table.get("business_hints", []),
                    "column_analysis": table.get("column_analysis", {}),
                    "sample_data": table.get("sample_data", []),
                    "relationship_info": full_relationship_metrics
                }
                clustering_tables.append(clustering_table)
            
            print(f"Prepared {len(clustering_tables)} tables for clustering analysis")
            return clustering_tables
            
        except Exception as e:
            print(f"Error preparing tables for clustering: {str(e)}")
            return []