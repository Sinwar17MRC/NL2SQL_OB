from sqlalchemy import create_engine, inspect, text 
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from typing import Dict, List, Any, Set
from datetime import datetime
import re
from collections import defaultdict
from functools import lru_cache

UNSUPPORTED_TYPE_NAMES = {
    "geography",
    "geometry",
    "hierarchyid",
    "sql_variant",
    "xml"
}

# NLTK import handling
try:
    from nltk.stem import WordNetLemmatizer
    LEMMATIZER = WordNetLemmatizer()
    NLTK_AVAILABLE = True
except ImportError:
    LEMMATIZER = None
    NLTK_AVAILABLE = False

# Logging setup
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

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

            self.raw_type_map = self._fetch_raw_column_types()
            
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
                self.logger.info(f"SUCCESS: Connection to {self.engine.url.host} verified.")
                return True
        except SQLAlchemyError as e:
            self.logger.error(f"SQLAlchemy connection failed: {e}")
            raise ConnectionError(f"Database connection failed. Check URL/credentials. Error: {e}")

    def _fetch_raw_column_types(self) -> Dict[str, Dict[str, str]]:
        """
        Queries the database's information schema directly to get the ground-truth
        data type for every column. This is our fallback for when SQLAlchemy's
        inspector fails on unsupported types.
        """
        self.logger.info("Pre-fetching raw column data types from information_schema...")
        type_map = {}
        query = text("""
            SELECT
                TABLE_SCHEMA,
                TABLE_NAME,
                COLUMN_NAME,
                DATA_TYPE
            FROM
                information_schema.columns
        """)
        try:
            with self.engine.connect() as connection:
                result = connection.execute(query)
                for row in result:
                    table_key = f"{row.TABLE_SCHEMA}.{row.TABLE_NAME}"
                    if table_key not in type_map:
                        type_map[table_key] = {}
                    type_map[table_key][row.COLUMN_NAME] = row.DATA_TYPE
            self.logger.info(f"Successfully cached raw types for {len(type_map)} tables.")
            return type_map
        except Exception as e:
            self.logger.error(f"Could not fetch raw column types from information_schema: {e}")
            return {} # Return an empty map on failure

    def _get_table_row_count(self, schema_name: str, table_name: str) -> int:
        """Simple row counting function"""
        try:
            with self.engine.connect() as connection:
                query = text(f'SELECT COUNT(*) FROM [{schema_name}].[{table_name}]')
                result = connection.execute(query)
                row_count = result.fetchone()[0]
                return int(row_count) if row_count else 0
        except Exception as e:
            self.logger.error(f"Row count failed for {schema_name}.{table_name}: {e}")
            return 0
    
    def get_schema_overview(self) -> Dict[str, Any]:
        """
        Get overview showing the TOP 2 SCHEMAS by total row count.
        Each schema shows its top 3 most dense tables.
        """
        try:
            schema_names = self.inspector.get_schema_names()
            schema_summaries = []
            
            self.logger.info(f"Found schemas: {schema_names}")
            
            # Step 1: For each schema, get top 3 tables and calculate total rows
            for schema_name in schema_names:
                # Skip system schemas
                if schema_name.lower() in ['information_schema', 'sys', 'guest']:
                    self.logger.info(f"Skipping system schema: {schema_name}")
                    continue
                    
                table_names = self.inspector.get_table_names(schema=schema_name)
                if not table_names:
                    self.logger.info(f"Schema '{schema_name}' has no tables, skipping")
                    continue
                    
                schema_tables = []
                self.logger.info(f"Processing schema '{schema_name}' with {len(table_names)} tables")
                
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
                        self.logger.warning(f"Could not process table '{schema_name}.{table_name}': {e}")
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
                
                self.logger.info(f"Schema '{schema_name}': {len(top_3_tables)} tables, {total_rows:,} total rows")
                for table in top_3_tables:
                    self.logger.info(f"  - {table['name']}: {table['row_count']:,} rows")
            
            if not schema_summaries:
                self.logger.warning("No schemas with processable tables found!")
                return {"tables": []}
            
            # Step 2: Sort schemas by total row count and take top 2
            schema_summaries.sort(key=lambda x: x['total_row_count'], reverse=True)
            
            self.logger.info(f"Schema ranking by total row count:")
            for i, schema in enumerate(schema_summaries):
                self.logger.info(f"  {i+1}. {schema['schema_name']}: {schema['total_row_count']:,} total rows")
            
            # Take top 2 schemas (or all if less than 2)
            top_schemas = schema_summaries[:2]
            self.logger.info(f"Selected top {len(top_schemas)} schemas")
            
            # Step 3: Collect all tables from the selected schemas
            final_tables = []
            for schema_summary in top_schemas:
                final_tables.extend(schema_summary['top_tables'])
                self.logger.info(f"Added {len(schema_summary['top_tables'])} tables from schema '{schema_summary['schema_name']}'")
            
            self.logger.info(f"Overview: Returning {len(final_tables)} tables from {len(top_schemas)} schemas")
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
                
                if not table_names:
                    self.logger.info(f"Schema '{schema_name}' has no tables, skipping for detailed view.")
                    continue
                
                self.logger.info(f"Getting detailed info for schema '{schema_name}' with {len(table_names)} tables")
                
                for table_name in table_names:
                    try:
                        # Get full column details
                        columns = []
                        for col in self.inspector.get_columns(table_name, schema=schema_name):
                    
                            # Use the raw type map as a fallback ---
                            sa_type_str = str(col['type'])
                            definitive_type_name = sa_type_str

                            if sa_type_str.lower() == 'null':
                                table_key = f"{schema_name}.{table_name}"
                                col_name = col['name']
                                # Look up the real type from our pre-fetched map
                                raw_name = self.raw_type_map.get(table_key, {}).get(col_name)
                                if raw_name:
                                    self.logger.debug(f"SQLAlchemy returned NULL for {table_key}.{col_name}. Using raw type: '{raw_name}'")
                                    definitive_type_name = raw_name
                                else:
                                    self.logger.warning(f"Could not find raw type for {table_key}.{col_name}, treating as unsupported.")
                                    definitive_type_name = "unknown" # rare
                            
                            # perform the check on the definitive, non-NULL type name
                            is_supported = definitive_type_name.lower() not in UNSUPPORTED_TYPE_NAMES and definitive_type_name.lower() != "unknown"
                            
                            column_info = {
                                "name": col['name'], 
                                "type": definitive_type_name, # Store the most accurate type we found
                                "supported": is_supported
                            }
                            
                            if not is_supported:
                                column_info["notes"] = f"Unsupported for direct query. Access value via the {col['name']}.ToString() method in SQL."
                            
                            columns.append(column_info)

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
                        self.logger.warning(f"Could not process table '{schema_name}.{table_name}': {e}")
                        continue

            self.logger.info(f"Detailed schema: Returning {len(all_tables)} tables from all schemas")
            return {"tables": all_tables}
            
        except SQLAlchemyError as e:
            raise ConnectionError(f"Failed to build detailed schema. Error: {e}")

    def dispose_engine(self):
        " cleaning up the engine connection's pool"
        if self.engine:
            self.engine.dispose()
            self.logger.info("SQLAlchemy engine disposed.")
    
    def get_enhanced_schema_for_rag(self) -> Dict[str, Any]:
        """
        Enhanced version of get_detailed_schema with RAG-specific information.
        Builds on existing detailed schema extraction.
        """
        try:
            base_schema = self.get_detailed_schema()
            
            if not base_schema or not base_schema.get("tables"):
                return base_schema
            
            self.logger.info(f"Enhancing schema with RAG-specific data for {len(base_schema['tables'])} tables")
            
            # Enhance each table with RAG-specific data
            enhanced_tables = []
            
            for table in base_schema["tables"]:
                enhanced_table = table.copy()
                try:
                    schema_name = table.get("schema", "")
                    table_name = table.get("table_name", "")
                    columns_info = table.get("columns", "")
                    
                    self.logger.info(f"Processing table: {schema_name}.{table_name}")
                    
                    # Add business context hints
                    enhanced_table["business_hints"] = self._extract_business_hints(table)
                    
                    # Add sample data for context
                    enhanced_table["sample_data"] = self._get_sample_data_safe(schema_name, table_name, columns_info)
                    
                    # Add relationship analysis
                    analysis_result = self._analyze_table_relationships(table)
                    enhanced_table["relationships_info"] = analysis_result["relationships"]

                    # Add row count 
                    enhanced_table["row_count"] = self._get_table_row_count(schema_name, table_name)
                    
                    enhanced_tables.append(enhanced_table)
                    
                except Exception as e:
                    self.logger.warning(f"Could not enhance table {schema_name}.{table_name}: {e}")
                    # Add the table without enhancements
                    enhanced_tables.append(table)
            
            # Create enhanced schema structure
            enhanced_schema = {
                "tables": enhanced_tables,
                "enhanced_at": datetime.now().isoformat()
            }
            
            self.logger.info(f"Schema enhancement completed successfully")
            return enhanced_schema
            
        except Exception as e:
            self.logger.error(f"Error enhancing schema for RAG: {str(e)}")
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
                ]
            },
            "order_processing": {
                "patterns": [
                    "order", "purchase", "transaction", "sale", "invoice", 
                    "receipt", "payment", "checkout", "cart", "basket",
                    "commande", "achat", "transaction", "vente", "facture",
                    "recu", "paiement", "panier", "caisse"
                ]
            },
            "inventory_management": {
                "patterns": [
                    "product", "item", "inventory", "stock", "catalog", 
                    "merchandise", "goods", "sku", "variant", "asset",
                    "produit", "article", "inventaire", "stock", "catalogue",
                    "marchandise", "bien", "variante", "actif"
                ]
            },
            "financial_data": {
                "patterns": [
                    "payment", "billing", "invoice", "finance", "accounting", 
                    "revenue", "expense", "budget", "cost", "price",
                    "paiement", "facturation", "facture", "finance", "comptabilite",
                    "revenu", "depense", "budget", "cout", "prix"
                ]
            },
            "hr_management": {
                "patterns": [
                    "employee", "staff", "department", "role", "position", 
                    "salary", "payroll", "benefit", "leave", "attendance",
                    "employe", "personnel", "departement", "role", "poste",
                    "salaire", "paie", "avantage", "conge", "presence"
                ]
            },
            "logistics": {
                "patterns": [
                    "shipping", "delivery", "warehouse", "supplier", "vendor", 
                    "transport", "freight", "shipment", "location", "address",
                    "livraison", "entrepot", "fournisseur", "vendeur",
                    "transport", "fret", "expedition", "localisation", "adresse"
                ]
            },
            "content_management": {
                "patterns": [
                    "content", "article", "document", "media", "file", 
                    "page", "post", "comment", "message", "notification",
                    "contenu", "article", "document", "media", "fichier",
                    "page", "publication", "commentaire", "message", "notification"
                ]
            },
            "audit_tracking": {
                "patterns": [
                    "log", "audit", "history", "tracking", "event", 
                    "activity", "session", "trace", "monitor",
                    "journal", "audit", "historique", "suivi", "evenement",
                    "activite", "session", "trace", "surveillance"
                ]
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

    def _extract_business_hints(self, table_info: Dict[str, Any]) -> List[str]:
        """Enhanced business intelligence extraction with confidence scoring."""
        table_name = table_info.get("table_name", "")
        columns = table_info.get("columns", [])
        hints = []

        table_tokens = self.get_enhanced_tokens(table_name)
        all_columns_tokens=set()
        for col in columns :
            all_columns_tokens.update(self.get_enhanced_tokens(col["name"]))
        
        # Advanced domain classification
        for domain_name, patterns in self.business_domains.items(): 
            domain_tokens = set()
            for pattern in patterns :
                domain_tokens.update(self.get_enhanced_tokens(pattern))
            
            if domain_tokens.intersection(table_tokens) or domain_tokens.intersection(all_columns_tokens) :
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

    def _get_sample_data_safe(self, schema_name: str, table_name: str, columns_info: List[Dict[str, Any]], max_rows: int = 5) -> List[Dict[str, Any]]:
        """
        Fetches sample data, proactively converting unsupported data types (like GEOGRAPHY)
        to a readable string format at the database level. This ensures all columns are
        represented in the sample data without causing driver errors.
        
        Args:
            schema_name: The name of the database schema.
            table_name: The name of the table.
            columns_info: A list of column dictionaries, each annotated with a
                        'supported': True/False flag.
            max_rows: The maximum number of sample rows to return.
            
        Returns:
            A list of dictionaries representing sample rows, with special types as strings.
        """
        full_table_name = f"[{schema_name}].[{table_name}]"
        self.logger.info(f"==> Fetching sample data for: {full_table_name}")

        # --- Step 1: Dynamically build the SELECT clause ---
        select_expressions = []
        for col in columns_info:
            col_name = col["name"]
            # The AS clause is crucial to ensure the column name in the result set is clean
            if col.get("supported", True):
                select_expressions.append(f"[{col_name}]")
            else:
                # Proactively convert the unsupported type to a string representation.
                expression = f"[{col_name}].ToString() AS [{col_name}]"
                select_expressions.append(expression)
                self.logger.info(f"  Applying .ToString() conversion for unsupported column: [{col_name}]")

        if not select_expressions:
            self.logger.warning(f"  No columns to select for {full_table_name}. Cannot fetch sample data.")
            return []
        
        column_list_str = ', '.join(select_expressions)

        try:
            with self.engine.connect() as connection:
                
                # --- Step 2: Execute initial query to determine table size ---
                try:
                    initial_query_str = f'SELECT TOP 101 {column_list_str} FROM {full_table_name}'
                    self.logger.info(f"  Executing initial check query with conversions.")
                    
                    result_proxy = connection.execute(text(initial_query_str))
                    columns = list(result_proxy.keys())
                    rows = result_proxy.fetchall()
                    self.logger.info(f"  Initial check query returned {len(rows)} rows.")

                except Exception as query_error:
                    self.logger.error(f"  Query execution failed for {full_table_name} despite conversions: {query_error}", exc_info=True)
                    return []

                if not rows:
                    return []
                
                # --- Step 3: Determine and execute final sampling strategy ---
                final_rows = []
                if len(rows) > 100:
                    self.logger.info(f"    Large table detected. Using random sampling.")
                    try:
                        sample_query_str = f'SELECT TOP {max_rows} {column_list_str} FROM {full_table_name} ORDER BY NEWID()'
                        sample_result = connection.execute(text(sample_query_str))
                        final_rows = sample_result.fetchall()
                    except Exception as sample_error:
                        self.logger.error(f"    Random sampling failed for {full_table_name}: {sample_error}")
                        final_rows = rows[:max_rows]
                else:
                    self.logger.info(f"    Small table detected. Using the initially fetched rows.")
                    final_rows = rows[:max_rows]

                # --- Step 4: Convert rows to dictionaries with privacy protection ---
                sample_data = []
                for row in final_rows:
                    row_dict = dict(zip(columns, row))
                    for col_name, value in row_dict.items():
                        if self._is_sensitive_column(col_name.lower()):
                            row_dict[col_name] = "[SENSITIVE_DATA]"
                        elif value is not None:
                            str_value = str(value)
                            row_dict[col_name] = str_value[:50] + "..." if len(str_value) > 50 else str_value
                    sample_data.append(row_dict)
                
                self.logger.info(f"    Successfully processed and returning {len(sample_data)} sample rows from {full_table_name}.")
                return sample_data
                
        except Exception as e:
            self.logger.error(f"!!! UNEXPECTED ERROR in _get_sample_data_safe for {full_table_name}: {e}", exc_info=True)
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
                
                # Enhanced pattern matching using token intersection
                if col_tokens.intersection(business_tokens):
                    relationship_type = "core_business"
                
                elif col_tokens.intersection(audit_tokens):
                    relationship_type = "audit"
                
                elif col_tokens.intersection(lookup_tokens):
                    relationship_type = "lookup"
                
                elif col_tokens.intersection(hierarchical_tokens):
                    relationship_type = "hierarchical"
                
                # Enhanced ID pattern analysis
                elif col_tokens.intersection({"id", "identifier", "key"}):
                    if referred_table_tokens.intersection(business_tokens):
                        relationship_type = "core_business"
                    elif referred_table_tokens.intersection(lookup_tokens):
                        relationship_type = "lookup"
                    elif referred_table_tokens.intersection(audit_tokens):
                        relationship_type = "audit"
                    else:
                        relationship_type = "lookup_reference"
                
                elif col_tokens.intersection({"key", "business", "external", "reference"}):
                    relationship_type = "business_key"
                
                # Create relationship record
                relationship = {
                    "target_table": referred_table,
                    "target_schema": referred_schema,
                    "via_column": column_name,
                    "relationship_type": relationship_type,
                    "is_cross_schema": referred_schema != table_info.get("schema", "")
                }
                
                relationships.append(relationship)
        
        return {
            "relationships": relationships
        }
    

    def get_tables_for_clustering(self) -> List[Dict[str, Any]]:
        """
        Extracts only the essential information from the schema needed for
        graph-based clustering: table IDs and their foreign key relationships.
        """
        try:
            base_schema = self.get_detailed_schema()
            
            if not base_schema or not base_schema.get("tables"):
                self.logger.warning("No schema data available for clustering")
                return []
            
            clustering_data = []
            
            for table in base_schema["tables"]:
                schema_name = table.get("schema", "")
                foreign_keys = table.get("foreign_keys", [])
                
                # Add cross-schema flag to each FK
                enhanced_fks = []
                for fk in foreign_keys:
                    fk_copy = fk.copy()
                    fk_copy["is_cross_schema"] = (
                        fk.get("referred_schema", schema_name) != schema_name
                    )
                    enhanced_fks.append(fk_copy)
                
                clustering_data.append({
                    "schema_name": schema_name,
                    "table_name": table.get("table_name", ""),
                    "full_table_id": f"{schema_name}.{table.get('table_name', '')}",
                    "foreign_keys": enhanced_fks,
                })
            
            self.logger.info(f"Prepared {len(clustering_data)} tables with essential data for clustering.")
            return clustering_data
            
        except Exception as e:
            self.logger.error(f"Error preparing tables for clustering: {str(e)}")
            return []